"""Durable dashboard configuration defaults and persistence."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import streamlit as st

from github_sync import GitHubStore

CONFIG_PATH = "app_config.json"
DEFAULT_CONFIG: dict[str, Any] = {
    "drive_folder_url": "",
    "drive_files": {
        "this_year_sales": {"file_id": "", "filename": "This Year Sales.parquet"},
        "last_year_sales": {"file_id": "", "filename": "Last Year Sales.parquet"},
        "week_on_week_soh": {"file_id": "", "filename": "Week on week SOH.parquet"},
        "location_master": {"file_id": "", "filename": "Location Master.xlsx"},
        "calendar": {"file_id": "", "filename": "Calender.xlsx"},
    },
    "visible_columns": {"YOY Sales": [], "SOH": []},
    "default_view": {
        "model": "YOY Sales", "rows": ["BTS WEEK"], "columns": [],
        "values": [{"field": "TY NSQ", "agg": "Sum"}, {"field": "LY NSQ", "agg": "Sum"}],
        "filters": [],
    },
    "row_filters": {"include_dc": True, "include_non_bbz": True, "include_non_operating": True},
    "report_date": None,
    "last_refreshed": None,
    "last_row_counts": {},
}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(store: GitHubStore) -> dict[str, Any]:
    saved = store.read_json(CONFIG_PATH, {}) or {}
    config = _deep_merge(DEFAULT_CONFIG, saved)
    try:
        drive_secrets = dict(st.secrets.get("drive", {}))
        if not config["drive_folder_url"]:
            config["drive_folder_url"] = str(drive_secrets.get("folder_url", ""))
        for key, item in dict(drive_secrets.get("files", {})).items():
            if key in config["drive_files"] and not config["drive_files"][key].get("file_id"):
                config["drive_files"][key].update(dict(item))
    except Exception:
        pass
    if not saved:
        save_config(store, config, "Seed dashboard configuration")
    return config


def save_config(store: GitHubStore, config: dict[str, Any], message: str) -> None:
    store.write_json(CONFIG_PATH, config, message)
