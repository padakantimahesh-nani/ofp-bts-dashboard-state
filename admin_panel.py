"""Administrator UI: users, sources, field exposure, defaults, and row rules."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st

from auth import hash_password, load_credentials, save_credentials
from config_store import load_config, save_config
from data_loader import TABLE_KEYS, load_all_data
from github_sync import GitHubStore


def _user_management(store: GitHubStore) -> None:
    st.subheader("User Management")
    credentials = load_credentials(store)
    users = credentials["users"]
    st.dataframe([{"Username": name, "Display name": value.get("display_name", ""), "Role": value.get("role", "user")}
                  for name, value in users.items()], width="stretch", hide_index=True)

    with st.expander("Add user"):
        with st.form("add_user", clear_on_submit=True):
            username = st.text_input("Username")
            display_name = st.text_input("Display name")
            password = st.text_input("Temporary password", type="password")
            role = st.selectbox("Role", ["user", "admin"])
            if st.form_submit_button("Add user"):
                username = username.strip()
                if not username or not password:
                    st.error("Username and password are required.")
                elif username in users:
                    st.error("That username already exists.")
                else:
                    users[username] = {"display_name": display_name.strip() or username, "role": role,
                                       "password_hash": hash_password(password), "must_change_password": True}
                    save_credentials(store, credentials, f"Add dashboard user {username}")
                    st.success("User added.")
                    st.rerun()

    if users:
        with st.expander("Edit, reset, or delete user"):
            selected = st.selectbox("User", sorted(users), key="edit_user_name")
            current = users[selected]
            display = st.text_input("Display name", current.get("display_name", selected), key="edit_display")
            role = st.selectbox("Role", ["user", "admin"], index=0 if current.get("role") == "user" else 1,
                                key="edit_role")
            new_password = st.text_input("New password (leave blank to keep current)", type="password")
            col1, col2 = st.columns(2)
            if col1.button("Save user changes", width="stretch"):
                current.update({"display_name": display.strip() or selected, "role": role})
                if new_password:
                    current.update({"password_hash": hash_password(new_password), "must_change_password": True})
                save_credentials(store, credentials, f"Update dashboard user {selected}")
                st.success("User updated.")
                st.rerun()
            if col2.button("Delete user", type="secondary", width="stretch"):
                if selected == st.session_state.username:
                    st.error("You cannot delete the account currently signed in.")
                elif current.get("role") == "admin" and sum(u.get("role") == "admin" for u in users.values()) <= 1:
                    st.error("At least one admin account must remain.")
                else:
                    del users[selected]
                    save_credentials(store, credentials, f"Delete dashboard user {selected}")
                    st.success("User deleted.")
                    st.rerun()


def _data_sources(store: GitHubStore, config: dict[str, Any]) -> tuple[dict[str, Any], Any | None]:
    st.subheader("Data Source Control")
    with st.form("drive_sources"):
        config["drive_folder_url"] = st.text_input("Public Google Drive folder URL", config.get("drive_folder_url", ""))
        st.caption("Enter each public file's ID and exact filename. CSV/Parquet is strongly recommended for the three large facts.")
        for key in TABLE_KEYS:
            st.markdown(f"**{key.replace('_', ' ').title()}**")
            c1, c2 = st.columns(2)
            item = config["drive_files"][key]
            item["file_id"] = c1.text_input("File ID", item.get("file_id", ""), key=f"id_{key}")
            item["filename"] = c2.text_input("Filename", item.get("filename", ""), key=f"name_{key}")
        if st.form_submit_button("Save data sources"):
            save_config(store, config, "Update Drive data sources")
            st.success("Data-source settings saved.")

    bundle = None
    if st.button("Refresh Data from Drive", type="primary"):
        st.cache_data.clear()
        try:
            token = int(datetime.now(timezone.utc).timestamp())
            bundle = load_all_data(json.dumps(config["drive_files"], sort_keys=True), token)
            config["last_refreshed"] = datetime.now(timezone.utc).isoformat()
            config["last_row_counts"] = {key: len(frame) for key, frame in bundle.tables.items()}
            save_config(store, config, "Record Drive data refresh")
            st.success("All five sources loaded successfully.")
        except Exception as exc:
            st.error(f"Refresh failed: {exc}")
    if config.get("last_refreshed"):
        st.caption(f"Last successful refresh (UTC): {config['last_refreshed']}")
        st.dataframe([{"Table": k, "Rows": v} for k, v in config.get("last_row_counts", {}).items()],
                     hide_index=True, width="stretch")
    return config, bundle


def _get_bundle(config: dict[str, Any], existing: Any | None) -> Any | None:
    if existing is not None:
        return existing
    try:
        return load_all_data(json.dumps(config["drive_files"], sort_keys=True), 0)
    except Exception as exc:
        st.warning(f"Configure and refresh all data sources before setting fields: {exc}")
        return None


def _report_date_control(store: GitHubStore, config: dict[str, Any], bundle: Any | None) -> None:
    st.subheader("Global Report Date")
    if bundle is None:
        return
    with st.form("global_report_date"):
        ty_dates = pd.to_datetime(bundle.tables["this_year_sales"].get("Date"), errors="coerce").dropna()
        if ty_dates.empty:
            st.error("This Year Sales has no valid Date values, so Report Date cannot be configured.")
            return
        min_date, max_date = ty_dates.min().date(), ty_dates.max().date()
        saved_report_date = pd.to_datetime(config.get("report_date"), errors="coerce")
        selected_default = max_date if pd.isna(saved_report_date) else saved_report_date.date()
        selected_default = min(max(selected_default, min_date), max_date)
        report_date = st.date_input(
            "Report Date",
            value=selected_default,
            min_value=min_date,
            max_value=max_date,
            help="Every fixed report and custom YOY view is capped at this TY date. LY uses the matching BTS week/day position.",
        )
        st.caption(f"Available TY date range: {min_date:%d %b %Y} to {max_date:%d %b %Y}")
        if st.form_submit_button("Save Report Date", type="primary"):
            config["report_date"] = report_date.isoformat()
            save_config(store, config, "Update global report date")
            st.success("Global Report Date saved. All user reports now use this cutoff.")


def _model_controls(store: GitHubStore, config: dict[str, Any], bundle: Any | None) -> None:
    st.subheader("User Pivot Configuration")
    if bundle is None:
        return
    with st.form("pivot_admin_config"):

        st.markdown("#### Column Visibility")
        for model, frame in bundle.models.items():
            current = config["visible_columns"].get(model) or list(frame.columns)
            config["visible_columns"][model] = st.multiselect(
                f"Fields exposed in {model}", list(frame.columns),
                default=[c for c in current if c in frame.columns], key=f"visible_{model}")

        st.markdown("#### Default View")
        default = config["default_view"]
        model_names = list(bundle.models)
        model = st.selectbox("Default model", model_names,
                             index=model_names.index(default.get("model")) if default.get("model") in model_names else 0)
        allowed = config["visible_columns"].get(model) or list(bundle.models[model].columns)
        rows = st.multiselect("Default Rows", allowed, [x for x in default.get("rows", []) if x in allowed])
        columns = st.multiselect("Default Columns", allowed, [x for x in default.get("columns", []) if x in allowed])
        value_fields = st.multiselect("Default Values", allowed,
                                      [x["field"] for x in default.get("values", []) if x.get("field") in allowed])
        default_values = []
        for field in value_fields:
            previous = next((x.get("agg", "Sum") for x in default.get("values", []) if x.get("field") == field), "Sum")
            agg = st.selectbox(f"Aggregation: {field}", ["Sum", "Average", "Count"],
                               index=["Sum", "Average", "Count"].index(previous), key=f"default_agg_{field}")
            default_values.append({"field": field, "agg": agg})

        st.markdown("#### Row Inclusion Rules")
        rules = config["row_filters"]
        include_dc = st.toggle("Include LOC Type = DC", rules.get("include_dc", True))
        include_non_bbz = st.toggle("Include Store Brand = Non-BBZ", rules.get("include_non_bbz", True))
        include_non_operating = st.toggle("Include Status = Non-Operating", rules.get("include_non_operating", True))
        if st.form_submit_button("Save pivot configuration", type="primary"):
            if not rows or not default_values:
                st.error("The default view needs at least one Row and one Value.")
            else:
                config["default_view"] = {"model": model, "rows": rows, "columns": columns,
                                          "values": default_values, "filters": []}
                config["row_filters"] = {"include_dc": include_dc, "include_non_bbz": include_non_bbz,
                                         "include_non_operating": include_non_operating}
                save_config(store, config, "Update pivot configuration")
                st.success("Pivot configuration saved.")


def render_admin_panel(store: GitHubStore) -> None:
    st.title("Admin Panel")
    tabs = st.tabs(["Users", "Data Sources", "Report Settings", "Pivot Configuration"])
    with tabs[0]:
        _user_management(store)
    config = load_config(store)
    with tabs[1]:
        config, refreshed_bundle = _data_sources(store, config)
    with tabs[2]:
        bundle = _get_bundle(config, refreshed_bundle)
        _report_date_control(store, config, bundle)
    with tabs[3]:
        bundle = _get_bundle(config, refreshed_bundle)
        _model_controls(store, config, bundle)
