"""Fixed BTS review panels reconstructed from the approved Excel layouts."""
from __future__ import annotations

import io
import re
from typing import Any, Iterable

import numpy as np
import pandas as pd
import streamlit as st

from data_loader import DataBundle, apply_admin_row_filters, canonical_week

METRIC_INPUTS = ["TY NSQ", "TY NSV", "TY COGS", "LY NSQ", "LY NSV", "LY COGS"]
DISPLAY_METRICS = [
    "INV QTY", "TY NSQ", "LY NSQ", "NSQ Growth", "TY NSV", "LY NSV", "NSV Growth",
    "TY GM%", "LY GM%", "GMV Growth", "WOC", "WROS", "TY ASP", "LY ASP", "ST%",
]
PERCENT_COLUMNS = {"NSQ Growth", "NSV Growth", "TY GM%", "LY GM%", "GMV Growth", "ST%"}


def _week_number(value: Any) -> int:
    match = re.search(r"(\d+)", str(value))
    return int(match.group(1)) if match else 9999


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.div(denominator.replace(0, np.nan))


def _field(df: pd.DataFrame, *names: str) -> str | None:
    return next((name for name in names if name in df.columns), None)


def _options(df: pd.DataFrame, column: str | None) -> list[Any]:
    if not column:
        return []
    values = df[column].dropna().unique().tolist()
    return sorted(values, key=lambda value: str(value).upper())


def _select_default(options: list[Any], wanted: Iterable[str]) -> list[Any]:
    wanted_upper = {str(x).upper() for x in wanted}
    return [x for x in options if str(x).upper() in wanted_upper]


def _filter_selected(df: pd.DataFrame, column: str | None, values: list[Any]) -> pd.DataFrame:
    return df if not column or not values else df[df[column].isin(values)]


def _clear_button(panel: str) -> int:
    version_key = f"{panel}_filter_version"
    version = int(st.session_state.get(version_key, 0))
    if st.button("Clear Selection", key=f"{panel}_clear_{version}", type="secondary"):
        st.session_state[version_key] = version + 1
        st.rerun()
    return version


def _slicer(panel: str, version: int, label: str, options: list[Any], default: list[Any] | None = None) -> list[Any]:
    selected = st.pills(
        label,
        options,
        selection_mode="multi",
        default=default or [],
        key=f"{panel}_{label}_{version}",
    )
    return list(selected or [])


def _inject_report_css() -> None:
    """Match the approved Excel slicer palette without introducing new colours."""
    st.markdown(
        """
        <style>
        div[data-testid="stPills"] button {
            background: #ffffff !important;
            border: 1.5px solid #1f2937 !important;
            border-radius: 5px !important;
            color: #111827 !important;
            min-height: 32px !important;
            padding: 4px 18px !important;
            font-weight: 500 !important;
        }
        div[data-testid="stPills"] button[aria-pressed="true"] {
            background: #83c9e6 !important;
            border-color: #39799a !important;
            color: #0b2333 !important;
        }
        div[data-testid="stPills"] button:hover {
            background: #b7e3f4 !important;
            border-color: #39799a !important;
        }
        div[data-testid="stDataFrame"] {
            border: 1px solid #39799a;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def report_context(bundle: DataBundle, config: dict[str, Any]) -> dict[str, Any]:
    calendar = bundle.tables["calendar"].copy()
    ty_dates = pd.to_datetime(bundle.tables["this_year_sales"]["Date"], errors="coerce").dropna()
    if ty_dates.empty:
        raise ValueError("This Year Sales contains no valid Date values.")
    start_date = ty_dates.min().normalize()
    maximum = ty_dates.max().normalize()
    configured = pd.to_datetime(config.get("report_date"), errors="coerce")
    report_date = maximum if pd.isna(configured) else pd.Timestamp(configured).normalize()
    report_date = min(max(report_date, start_date), maximum)
    cal = calendar[pd.to_datetime(calendar["Date"], errors="coerce") <= report_date].sort_values("Date")
    if cal.empty:
        raise ValueError("Calendar does not contain a row on or before the selected Report Date.")
    row = cal.iloc[-1]
    report_week = canonical_week(row.get("BTS WEEK"))
    report_day = pd.to_numeric(pd.Series([row.get("DAY")]), errors="coerce").iloc[0]
    # Match the approved Excel formula exactly: Report Date - Calendar Starting Date.
    elapsed_days = max((report_date - start_date).days, 1)
    return {
        "report_date": report_date,
        "start_date": start_date,
        "report_week": report_week,
        "report_week_number": _week_number(report_week),
        "report_day": report_day,
        "elapsed_days": elapsed_days,
    }


def filter_yoy_as_of(df: pd.DataFrame, context: dict[str, Any]) -> pd.DataFrame:
    """Cap TY by date and LY by the equivalent BTS week/day comparison point."""
    output = df.copy()
    ty_date = pd.to_datetime(output.get("TY Date"), errors="coerce")
    week_number = output["BTS WEEK"].map(_week_number)
    day = pd.to_numeric(output.get("DAY"), errors="coerce")
    comparable = (week_number < context["report_week_number"]) | (
        (week_number == context["report_week_number"]) & (day <= context["report_day"])
    )
    mask = ty_date.le(context["report_date"]) | (ty_date.isna() & comparable)
    return output.loc[mask].copy()


def filter_soh_as_of(df: pd.DataFrame, context: dict[str, Any], weekly: bool = False) -> pd.DataFrame:
    output = df.copy()
    week_number = output["BTS WEEK"].map(_week_number)
    if weekly:
        return output.loc[week_number <= context["report_week_number"]].copy()
    exact = output.loc[week_number == context["report_week_number"]].copy()
    if not exact.empty:
        return exact
    available = week_number[week_number <= context["report_week_number"]]
    return output.loc[week_number == available.max()].copy() if not available.empty else output.iloc[0:0].copy()


def _metrics_table(yoy: pd.DataFrame, soh: pd.DataFrame, groups: list[str], elapsed_days: int) -> pd.DataFrame:
    for frame in (yoy, soh):
        for group in groups:
            if group not in frame.columns:
                frame[group] = pd.NA
    if groups:
        sales = yoy.groupby(groups, dropna=False, observed=True)[METRIC_INPUTS].sum().reset_index()
        inv = soh.assign(**{"Inventory Qty": pd.to_numeric(soh.get("Inventory Qty"), errors="coerce").fillna(0)})
        inv = inv.groupby(groups, dropna=False, observed=True)["Inventory Qty"].sum().reset_index()
        result = sales.merge(inv, on=groups, how="outer")
    else:
        values = {metric: pd.to_numeric(yoy.get(metric), errors="coerce").fillna(0).sum() for metric in METRIC_INPUTS}
        values["Inventory Qty"] = pd.to_numeric(soh.get("Inventory Qty"), errors="coerce").fillna(0).sum()
        result = pd.DataFrame([values])
    for metric in METRIC_INPUTS + ["Inventory Qty"]:
        result[metric] = pd.to_numeric(result.get(metric), errors="coerce").fillna(0)
    result["INV QTY"] = result.pop("Inventory Qty")
    result["NSQ Growth"] = _safe_divide(result["TY NSQ"], result["LY NSQ"]) - 1
    result["NSV Growth"] = _safe_divide(result["TY NSV"], result["LY NSV"]) - 1
    result["TY GM%"] = 1 - _safe_divide(result["TY COGS"], result["TY NSV"])
    result["LY GM%"] = 1 - _safe_divide(result["LY COGS"], result["LY NSV"])
    ty_gmv = result["TY NSV"] - result["TY COGS"]
    ly_gmv = result["LY NSV"] - result["LY COGS"]
    result["GMV Growth"] = _safe_divide(ty_gmv, ly_gmv) - 1
    result["WROS"] = result["TY NSQ"] / max(elapsed_days, 1) * 7
    result["WOC"] = _safe_divide(result["INV QTY"], result["WROS"])
    result["TY ASP"] = _safe_divide(result["TY NSV"], result["TY NSQ"])
    result["LY ASP"] = _safe_divide(result["LY NSV"], result["LY NSQ"])
    result["ST%"] = _safe_divide(result["TY NSQ"], result["TY NSQ"] + result["INV QTY"])
    return result[groups + DISPLAY_METRICS]


def _append_total(detail: pd.DataFrame, yoy: pd.DataFrame, soh: pd.DataFrame, groups: list[str], label_column: str,
                  label: str, elapsed_days: int) -> pd.DataFrame:
    total = _metrics_table(yoy, soh, [], elapsed_days)
    for group in groups:
        total[group] = ""
    total[label_column] = label
    return pd.concat([detail, total[groups + DISPLAY_METRICS]], ignore_index=True)


def _column_config(frame: pd.DataFrame) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for column in frame.columns:
        if column in PERCENT_COLUMNS or any(column.endswith(f" | {metric}") for metric in PERCENT_COLUMNS):
            config[column] = st.column_config.NumberColumn(column, format="%.1f%%")
        elif column in {"TY ASP", "LY ASP", "WOC", "WROS"} or any(column.endswith(f" | {m}") for m in ("TY ASP", "LY ASP", "WOC", "WROS")):
            config[column] = st.column_config.NumberColumn(column, format="%.1f")
        elif pd.api.types.is_numeric_dtype(frame[column]):
            config[column] = st.column_config.NumberColumn(column, format="%,.0f")
    return config


def _table_styler(frame: pd.DataFrame, subtotal_fill: str | None = None) -> pd.io.formats.style.Styler:
    blue = "#b7e3f4"
    green = "#d9ead3"
    border = "#39799a"

    def style_row(row: pd.Series) -> list[str]:
        labels = [str(value).strip().upper() for value in row.iloc[: min(3, len(row))] if pd.notna(value)]
        is_grand = any(value == "GRAND TOTAL" for value in labels)
        is_subtotal = any(value.endswith(" TOTAL") for value in labels) and not is_grand
        if is_grand:
            css = f"background-color: {blue}; font-weight: 700; border-top: 1px solid {border};"
        elif is_subtotal and subtotal_fill:
            css = f"background-color: {subtotal_fill}; font-weight: 700; border-top: 1px solid {border};"
        elif is_subtotal:
            css = f"background-color: #ffffff; font-weight: 700; border-top: 1px solid {border}; border-bottom: 1px solid {border};"
        else:
            css = "background-color: #ffffff;"
        return [css] * len(row)

    return (
        frame.style
        .apply(style_row, axis=1)
        .set_table_styles([
            {"selector": "th", "props": [("background-color", blue), ("color", "#000000"),
                                             ("font-weight", "700"), ("border", f"1px solid {border}")]},
            {"selector": "td", "props": [("border", "1px solid #d9d9d9")]},
        ])
    )


def _display_table(frame: pd.DataFrame, key: str, height: int = 520, subtotal_fill: str | None = None) -> None:
    show = frame.copy()
    for column in show.columns:
        if column in PERCENT_COLUMNS or any(column.endswith(f" | {metric}") for metric in PERCENT_COLUMNS):
            show[column] = show[column] * 100
    styled = _table_styler(show, subtotal_fill=subtotal_fill)
    st.dataframe(styled, use_container_width=True, hide_index=True, height=height, column_config=_column_config(show))
    st.download_button("Export CSV", show.to_csv(index=False).encode("utf-8-sig"), f"{key}.csv", "text/csv",
                       key=f"download_{key}")


def _country_and_type_slicers(panel: str, version: int, yoy: pd.DataFrame, defaults: Iterable[str]) -> tuple[list[Any], list[Any]]:
    country_col, type_col = _field(yoy, "Country", "LM Country"), _field(yoy, "BTS TYPE")
    countries, types = _options(yoy, country_col), _options(yoy, type_col)
    c1, c2 = st.columns(2)
    with c1:
        selected_countries = _slicer(panel, version, "Country", countries, [])
    with c2:
        selected_types = _slicer(panel, version, "BTS TYPE", types, _select_default(types, defaults) if version == 0 else [])
    return selected_countries, selected_types


def panel_department(yoy: pd.DataFrame, soh: pd.DataFrame, context: dict[str, Any]) -> None:
    st.header("Panel 1 · Department-wise BTS Performance")
    version = _clear_button("department")
    countries, bts_types = _country_and_type_slicers("department", version, yoy, ["KIDS FOOTWEAR", "BACKPACK"])
    for frame in (yoy, soh):
        frame.drop(frame.index.difference(_filter_selected(frame, _field(frame, "Country", "LM Country"), countries).index), inplace=True)
        frame.drop(frame.index.difference(_filter_selected(frame, _field(frame, "BTS TYPE"), bts_types).index), inplace=True)
    soh = filter_soh_as_of(soh, context, weekly=True)
    if yoy.empty and soh.empty:
        st.warning("No records match the selected filters.")
        return
    country_col = _field(yoy, "Country", "LM Country") or _field(soh, "Country", "LM Country") or "Country"
    detail = _metrics_table(yoy, soh, [country_col, "BTS WEEK"], context["elapsed_days"])
    detail["_week"] = detail["BTS WEEK"].map(_week_number)
    detail = detail.sort_values([country_col, "_week"]).drop(columns="_week")
    detail["Cumulative NSQ"] = detail.groupby(country_col, dropna=False)["TY NSQ"].cumsum()
    rows = []
    for country in detail[country_col].drop_duplicates():
        country_detail = detail[detail[country_col].eq(country)]
        rows.append(country_detail)
        total = _metrics_table(yoy[yoy[country_col].eq(country)], soh[soh[country_col].eq(country)], [], context["elapsed_days"])
        total[country_col], total["BTS WEEK"], total["Cumulative NSQ"] = f"{country} Total", "", total["TY NSQ"]
        rows.append(total[[country_col, "BTS WEEK"] + DISPLAY_METRICS + ["Cumulative NSQ"]])
    output = pd.concat(rows, ignore_index=True)
    grand = _metrics_table(yoy, soh, [], context["elapsed_days"])
    grand[country_col], grand["BTS WEEK"], grand["Cumulative NSQ"] = "Grand Total", "", grand["TY NSQ"]
    output = pd.concat([output, grand[[country_col, "BTS WEEK"] + DISPLAY_METRICS + ["Cumulative NSQ"]]], ignore_index=True)
    _display_table(output, "panel_1_department")


def panel_skechers(yoy: pd.DataFrame, soh: pd.DataFrame, context: dict[str, Any]) -> None:
    st.header("Panel 2 · Skechers Review")
    st.caption("Item Sub Brand is automatically fixed to Skechers.")
    version = _clear_button("skechers")
    countries, bts_types = _country_and_type_slicers("skechers", version, yoy, ["KIDS FOOTWEAR"])
    for name, frame in (("sales", yoy), ("stock", soh)):
        brand_col = _field(frame, "Item Sub Brand")
        if brand_col:
            frame.drop(frame.index[~frame[brand_col].astype("string").str.upper().eq("SKECHERS")], inplace=True)
        selected = _filter_selected(frame, _field(frame, "Country", "LM Country"), countries)
        selected = _filter_selected(selected, _field(frame, "BTS TYPE"), bts_types)
        frame.drop(frame.index.difference(selected.index), inplace=True)
    soh = filter_soh_as_of(soh, context, weekly=False)
    if yoy.empty and soh.empty:
        st.warning("No Skechers records match the selected filters.")
        return
    for frame in (yoy, soh):
        color_col = _field(frame, "Color")
        color = frame[color_col].astype("string").str.strip().str.upper() if color_col else pd.Series("", index=frame.index)
        frame["Color Group"] = np.select(
            [color.eq("BLACK").fillna(False).to_numpy(dtype=bool), color.eq("WHITE").fillna(False).to_numpy(dtype=bool)],
            ["BLACK", "WHITE"], default="Colorful"
        )
    color_table = _metrics_table(yoy, soh, ["Color Group"], context["elapsed_days"])
    color_table = _append_total(color_table, yoy, soh, ["Color Group"], "Color Group", "Grand Total", context["elapsed_days"])
    st.subheader("Colour Performance")
    _display_table(color_table, "panel_2_skechers_colour", 300)

    size_col = _field(yoy, "Ofp Size") or _field(soh, "Ofp Size")
    if size_col:
        size_table = _metrics_table(yoy, soh, [size_col], context["elapsed_days"]).sort_values("TY NSQ", ascending=False)
        size_table = _append_total(size_table, yoy, soh, [size_col], size_col, "Grand Total", context["elapsed_days"])
        st.subheader("Size Performance")
        _display_table(size_table, "panel_2_skechers_size", 400)


def panel_inventory(yoy: pd.DataFrame, soh: pd.DataFrame, context: dict[str, Any]) -> None:
    del yoy
    st.header("Panel 3 · BTS Type Inventory Review")
    version = _clear_button("inventory")
    soh = filter_soh_as_of(soh, context, weekly=False)
    country_col, loc_col, type_col = _field(soh, "Country", "LM Country"), _field(soh, "LOC Type", "LM Type"), _field(soh, "BTS TYPE")
    c1, c2, c3 = st.columns(3)
    with c1:
        countries = _slicer("inventory", version, "Country", _options(soh, country_col), [])
    with c2:
        loc_types = _slicer("inventory", version, "LOC Type", _options(soh, loc_col), [])
    with c3:
        bts_types = _slicer("inventory", version, "BTS TYPE", _options(soh, type_col), [])
    soh = _filter_selected(_filter_selected(_filter_selected(soh, country_col, countries), loc_col, loc_types), type_col, bts_types)
    if soh.empty:
        st.warning("No inventory records match the selected filters.")
        return
    detail = pd.pivot_table(soh, index=[country_col, loc_col], columns=type_col, values="Inventory Qty",
                            aggfunc="sum", fill_value=0, observed=True).reset_index()
    value_columns = [column for column in detail.columns if column not in {country_col, loc_col}]
    rows = []
    for country in detail[country_col].drop_duplicates():
        rows.append(detail[detail[country_col].eq(country)])
        total_values = soh[soh[country_col].eq(country)].groupby(type_col, observed=True)["Inventory Qty"].sum()
        total = {country_col: f"{country} Total", loc_col: ""}
        total.update({column: total_values.get(column, 0) for column in value_columns})
        rows.append(pd.DataFrame([total]))
    matrix = pd.concat(rows, ignore_index=True)
    grand_values = soh.groupby(type_col, observed=True)["Inventory Qty"].sum()
    grand = {country_col: "Grand Total", loc_col: ""}
    grand.update({column: grand_values.get(column, 0) for column in value_columns})
    matrix = pd.concat([matrix, pd.DataFrame([grand])], ignore_index=True)
    matrix.columns.name = None
    _display_table(matrix, "panel_3_inventory", subtotal_fill="#d9ead3")


def panel_brands(yoy: pd.DataFrame, soh: pd.DataFrame, context: dict[str, Any]) -> None:
    st.header("Panel 4 · Brands Review")
    st.caption("Top 10 brands are ranked by TY NSV after applying the current slicers.")
    version = _clear_button("brands")
    countries, bts_types = _country_and_type_slicers("brands", version, yoy, ["KIDS FOOTWEAR", "BACKPACK"])
    for frame in (yoy, soh):
        selected = _filter_selected(frame, _field(frame, "Country", "LM Country"), countries)
        selected = _filter_selected(selected, _field(frame, "BTS TYPE"), bts_types)
        frame.drop(frame.index.difference(selected.index), inplace=True)
    soh = filter_soh_as_of(soh, context, weekly=True)
    brand_col = _field(yoy, "Item Sub Brand") or _field(soh, "Item Sub Brand")
    if not brand_col:
        st.warning("Item Sub Brand is unavailable.")
        return
    top = yoy.groupby(brand_col, dropna=False)["TY NSV"].sum().nlargest(10).index.tolist()
    yoy, soh = yoy[yoy[brand_col].isin(top)], soh[soh[brand_col].isin(top)]
    detail = _metrics_table(yoy, soh, [brand_col, "BTS WEEK"], context["elapsed_days"])
    detail["_week"] = detail["BTS WEEK"].map(_week_number)
    detail = detail.sort_values([brand_col, "_week"]).drop(columns="_week")
    detail["Cumulative NSQ"] = detail.groupby(brand_col, dropna=False)["TY NSQ"].cumsum()
    keep_metrics = ["INV QTY", "TY NSQ", "TY NSV", "TY GM%", "WOC", "WROS", "TY ASP"]
    rows = []
    for brand in detail[brand_col].drop_duplicates():
        brand_detail = detail[detail[brand_col].eq(brand)]
        rows.append(brand_detail[[brand_col, "BTS WEEK"] + keep_metrics + ["Cumulative NSQ"]])
        total = _metrics_table(yoy[yoy[brand_col].eq(brand)], soh[soh[brand_col].eq(brand)], [], context["elapsed_days"])
        total[brand_col], total["BTS WEEK"], total["Cumulative NSQ"] = f"{brand} Total", "", total["TY NSQ"]
        rows.append(total[[brand_col, "BTS WEEK"] + keep_metrics + ["Cumulative NSQ"]])
    output = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if not output.empty:
        grand = _metrics_table(yoy, soh, [], context["elapsed_days"])
        grand[brand_col], grand["BTS WEEK"], grand["Cumulative NSQ"] = "Grand Total", "", grand["TY NSQ"]
        output = pd.concat([output, grand[[brand_col, "BTS WEEK"] + keep_metrics + ["Cumulative NSQ"]]], ignore_index=True)
    _display_table(output, "panel_4_brands")


def panel_stores(yoy: pd.DataFrame, soh: pd.DataFrame, context: dict[str, Any]) -> None:
    st.header("Panel 5 · Store-wise Performance")
    version = _clear_button("stores")
    soh = filter_soh_as_of(soh, context, weekly=False)
    country_col = _field(yoy, "Country", "LM Country") or "Country"
    location_col = _field(yoy, "LM Location Name", "Location", "LM Location") or "Location"
    lfl_col = _field(yoy, "LM LFL/NLFL", "LFL/NLFL")
    type_col = _field(yoy, "BTS TYPE")
    c1, c2, c3 = st.columns(3)
    with c1:
        countries = _slicer("stores", version, "Country", _options(yoy, country_col), [])
    with c2:
        lfl_values = _slicer("stores", version, "LFL/NLFL", _options(yoy, lfl_col), [])
    with c3:
        type_options = _options(yoy, type_col)
        selected_types = _slicer("stores", version, "BTS TYPE", type_options,
                                 _select_default(type_options, ["BACKPACK", "KIDS FOOTWEAR"]) if version == 0 else [])
    for frame in (yoy, soh):
        selected = _filter_selected(frame, _field(frame, "Country", "LM Country"), countries)
        selected = _filter_selected(selected, _field(frame, "LM LFL/NLFL", "LFL/NLFL"), lfl_values)
        frame.drop(frame.index.difference(selected.index), inplace=True)
    groups = [country_col, location_col]
    def store_piece(sales_part: pd.DataFrame, stock_part: pd.DataFrame, prefix: str) -> pd.DataFrame:
        detail = _metrics_table(sales_part, stock_part, groups, context["elapsed_days"])
        country_total = _metrics_table(sales_part, stock_part, [country_col], context["elapsed_days"])
        country_total[location_col] = ""
        country_total[country_col] = country_total[country_col].astype("string") + " Total"
        grand = _metrics_table(sales_part, stock_part, [], context["elapsed_days"])
        grand[country_col], grand[location_col] = "Grand Total", ""
        combined = pd.concat([detail, country_total[groups + DISPLAY_METRICS], grand[groups + DISPLAY_METRICS]], ignore_index=True)
        return combined.rename(columns={m: f"{prefix} | {m}" for m in DISPLAY_METRICS})

    pieces = []
    for bts_type in selected_types:
        sales_part = _filter_selected(yoy, type_col, [bts_type])
        stock_part = _filter_selected(soh, _field(soh, "BTS TYPE"), [bts_type])
        pieces.append(store_piece(sales_part, stock_part, str(bts_type)))
    pieces.append(store_piece(yoy, soh, "All BTS Types"))
    output = pieces[0]
    for piece in pieces[1:]:
        output = output.merge(piece, on=groups, how="outer")
    output["_is_grand"] = output[country_col].astype("string").eq("Grand Total").astype(int)
    output["_country_sort"] = output[country_col].astype("string").str.replace(r" Total$", "", regex=True)
    output["_row_rank"] = np.select(
        [output[country_col].astype("string").eq("Grand Total"), output[country_col].astype("string").str.endswith(" Total")],
        [2, 1], default=0,
    )
    output = output.sort_values(["_is_grand", "_country_sort", "_row_rank", location_col]).drop(
        columns=["_is_grand", "_country_sort", "_row_rank"]
    )
    _display_table(output, "panel_5_store_performance", 600)


PANEL_LABELS = [
    "Panel 1 · Department-wise",
    "Panel 2 · Skechers Review",
    "Panel 3 · BTS Type Inventory",
    "Panel 4 · Brands Review",
    "Panel 5 · Store Performance",
]


def render_fixed_panel(name: str, bundle: DataBundle, config: dict[str, Any]) -> None:
    _inject_report_css()
    context = report_context(bundle, config)
    st.info(
        f"Report Date: **{context['report_date']:%d %b %Y}** · "
        f"Comparison through **{context['report_week']} / Day {int(context['report_day'])}** · "
        f"BTS elapsed days: **{context['elapsed_days']}**"
    )
    yoy = apply_admin_row_filters(bundle.models["YOY Sales"], config["row_filters"])
    soh = apply_admin_row_filters(bundle.models["SOH"], config["row_filters"])
    yoy = filter_yoy_as_of(yoy, context)
    if name == PANEL_LABELS[0]:
        panel_department(yoy.copy(), soh.copy(), context)
    elif name == PANEL_LABELS[1]:
        panel_skechers(yoy.copy(), soh.copy(), context)
    elif name == PANEL_LABELS[2]:
        panel_inventory(yoy.copy(), soh.copy(), context)
    elif name == PANEL_LABELS[3]:
        panel_brands(yoy.copy(), soh.copy(), context)
    elif name == PANEL_LABELS[4]:
        panel_stores(yoy.copy(), soh.copy(), context)
