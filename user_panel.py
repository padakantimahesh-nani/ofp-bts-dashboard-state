"""Excel-like pivot builder, charts, exports, and saved configurations."""
from __future__ import annotations

import io
import json
import re
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from config_store import load_config
from data_loader import apply_admin_row_filters, load_all_data
from github_sync import GitHubStore
from report_panels import PANEL_LABELS, filter_soh_as_of, filter_yoy_as_of, render_fixed_panel, report_context

AGG_MAP = {"Sum": "sum", "Average": "mean", "Count": "count"}


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._")
    return cleaned[:80] or "view"


def _view_path(username: str, view_name: str) -> str:
    return f"saved_views/{_safe_component(username)}__{_safe_component(view_name)}.json"


def _list_views(store: GitHubStore, username: str) -> dict[str, str]:
    prefix = f"{_safe_component(username)}__"
    result = {}
    for item in store.list_directory("saved_views"):
        name = str(item.get("name", ""))
        if name.startswith(prefix) and name.endswith(".json"):
            result[name[len(prefix):-5].replace("_", " ")] = str(item["path"])
    return result


def _default_state(config: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(config["default_view"]))


def _set_pivot_state(value: dict[str, Any]) -> None:
    st.session_state.pivot_config = value
    st.session_state.pivot_widget_version = st.session_state.get("pivot_widget_version", 0) + 1


def _filter_values(series: pd.Series) -> list[Any]:
    values = series.dropna().unique().tolist()
    try:
        return sorted(values)
    except TypeError:
        return sorted(values, key=str)


def _build_pivot(df: pd.DataFrame, state: dict[str, Any]) -> pd.DataFrame:
    rows, columns = state["rows"], state["columns"]
    fields = [item["field"] for item in state["values"]]
    aggfunc = {item["field"]: AGG_MAP[item["agg"]] for item in state["values"]}
    return pd.pivot_table(df, index=rows or None, columns=columns or None, values=fields,
                          aggfunc=aggfunc, fill_value=0, dropna=False, observed=True, margins=False)


def _flatten_pivot(pivot: pd.DataFrame) -> pd.DataFrame:
    output = pivot.reset_index()
    if isinstance(output.columns, pd.MultiIndex):
        output.columns = [" | ".join(str(x) for x in col if str(x) not in {"", "None"}).strip(" |")
                          for col in output.columns]
    else:
        output.columns = [str(c) for c in output.columns]
    return output


def _excel_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        frame.to_excel(writer, sheet_name="Pivot", index=False)
    return buffer.getvalue()


def _render_chart(frame: pd.DataFrame, rows: list[str], value_fields: list[str]) -> None:
    if not rows or frame.empty:
        return
    x = rows[0] if rows[0] in frame.columns else frame.columns[0]
    numeric = [c for c in frame.columns if c != x and pd.api.types.is_numeric_dtype(frame[c])]
    if not numeric:
        return
    chart_type = st.radio("Chart", ["Bar", "Line"], horizontal=True)
    y = numeric[:12]
    fig = px.bar(frame, x=x, y=y, barmode="group") if chart_type == "Bar" else px.line(frame, x=x, y=y, markers=True)
    fig.update_layout(legend_title_text="Values", height=480)
    st.plotly_chart(fig, width="stretch")


def render_user_panel(store: GitHubStore) -> None:
    config = load_config(store)
    try:
        bundle = load_all_data(json.dumps(config["drive_files"], sort_keys=True), 0)
    except Exception as exc:
        st.error(f"Data is unavailable. Ask an administrator to check Data Sources. Details: {exc}")
        return

    report_page = st.sidebar.radio("Reports", PANEL_LABELS + ["Custom Pivot Builder"])
    if report_page in PANEL_LABELS:
        render_fixed_panel(report_page, bundle, config)
        return

    st.title("Custom Pivot Builder")
    context = report_context(bundle, config)
    st.info(f"Report Date: **{context['report_date']:%d %b %Y}** · All data is capped at the matching BTS comparison point.")

    if "pivot_config" not in st.session_state:
        _set_pivot_state(_default_state(config))
    state = st.session_state.pivot_config
    version = st.session_state.get("pivot_widget_version", 0)

    st.sidebar.subheader("Saved Views")
    try:
        views = _list_views(store, st.session_state.username)
    except Exception as exc:
        views = {}
        st.sidebar.warning(f"Saved views unavailable: {exc}")
    selected_view = st.sidebar.selectbox("Load View", ["— Select —"] + list(views), key=f"load_view_{version}")
    if st.sidebar.button("Apply saved view", disabled=selected_view == "— Select —"):
        loaded = store.read_json(views[selected_view])
        _set_pivot_state(loaded["configuration"])
        st.rerun()
    if st.sidebar.button("Clear Selection"):
        cleared = json.loads(json.dumps(state))
        cleared["filters"] = []
        _set_pivot_state(cleared)
        st.rerun()
    if st.sidebar.button("Reset Pivot Layout"):
        _set_pivot_state(_default_state(config))
        st.rerun()

    model_names = list(bundle.models)
    model = st.selectbox("Data model", model_names,
                         index=model_names.index(state.get("model")) if state.get("model") in model_names else 0,
                         key=f"model_{version}")
    df = apply_admin_row_filters(bundle.models[model], config["row_filters"])
    if model == "YOY Sales":
        df = filter_yoy_as_of(df, context)
    elif model == "SOH":
        df = filter_soh_as_of(df, context, weekly=False)
    allowed = [c for c in (config["visible_columns"].get(model) or list(df.columns)) if c in df.columns]
    if not allowed:
        st.warning("No fields are exposed for this model. Ask an administrator to enable columns.")
        return

    c1, c2 = st.columns(2)
    rows = c1.multiselect("Rows", allowed, [x for x in state.get("rows", []) if x in allowed], key=f"rows_{version}")
    columns = c2.multiselect("Columns", [x for x in allowed if x not in rows],
                             [x for x in state.get("columns", []) if x in allowed and x not in rows], key=f"cols_{version}")
    value_candidates = [c for c in allowed if pd.api.types.is_numeric_dtype(df[c])]
    previous_values = {x["field"]: x.get("agg", "Sum") for x in state.get("values", [])}
    value_fields = st.multiselect("Values", value_candidates, [x for x in previous_values if x in value_candidates],
                                  key=f"values_{version}")
    values = []
    if value_fields:
        agg_cols = st.columns(min(3, len(value_fields)))
        for idx, field in enumerate(value_fields):
            choice = previous_values.get(field, "Sum")
            agg = agg_cols[idx % len(agg_cols)].selectbox(f"{field} aggregation", list(AGG_MAP),
                                                          index=list(AGG_MAP).index(choice), key=f"agg_{version}_{field}")
            values.append({"field": field, "agg": agg})

    previous_filters = {x["field"]: x.get("values", []) for x in state.get("filters", [])}
    filter_fields = st.multiselect("Filters", [x for x in allowed if x not in value_fields],
                                   [x for x in previous_filters if x in allowed], key=f"filters_{version}")
    filters = []
    filtered = df
    for field in filter_fields:
        options = _filter_values(df[field])
        selected = st.multiselect(f"Filter values: {field}", options,
                                  [x for x in previous_filters.get(field, []) if x in options], key=f"filterval_{version}_{field}")
        filters.append({"field": field, "values": selected})
        if selected:
            filtered = filtered[filtered[field].isin(selected)]

    current = {"model": model, "rows": rows, "columns": columns, "values": values, "filters": filters}
    st.session_state.pivot_config = current
    if not rows and not columns:
        st.info("Choose at least one Rows or Columns field.")
        return
    if not values:
        st.info("Choose at least one numeric Values field.")
        return
    if filtered.empty:
        st.warning("The selected filters return no rows.")
        return
    try:
        pivot = _build_pivot(filtered, current)
        display = _flatten_pivot(pivot)
    except Exception as exc:
        st.error(f"Could not build this pivot: {exc}")
        return

    st.subheader("Pivot Result")
    st.caption(f"Source rows after admin and user filters: {len(filtered):,}")
    st.dataframe(display, width="stretch", hide_index=True, height=500)
    _render_chart(display, rows, value_fields)

    st.subheader("Save or Export")
    s1, s2, s3 = st.columns(3)
    view_name = s1.text_input("View name", placeholder="e.g. Weekly Quantity")
    if s1.button("Save View", type="primary", disabled=not view_name.strip()):
        path = _view_path(st.session_state.username, view_name)
        store.write_json(path, {"username": st.session_state.username, "view_name": view_name.strip(),
                                "configuration": current}, f"Save pivot view {view_name.strip()}")
        st.success("View saved.")
    s2.download_button("Export Excel", _excel_bytes(display), "bts_pivot.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    s3.download_button("Export CSV", display.to_csv(index=False).encode("utf-8-sig"), "bts_pivot.csv",
                       "text/csv", width="stretch")
