"""Download Drive sources, normalize fields, and build analysis-ready models."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import requests
import streamlit as st

TABLE_KEYS = ("this_year_sales", "last_year_sales", "week_on_week_soh", "location_master", "calendar")
SALES_KEY = ["BTS WEEK", "DAY", "Code", "Item Barcode"]


@dataclass
class DataBundle:
    tables: dict[str, pd.DataFrame]
    models: dict[str, pd.DataFrame]


def _drive_download_url(file_id: str) -> str:
    return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"


def _read_frame(raw: bytes, filename: str) -> pd.DataFrame:
    suffix = filename.lower().rsplit(".", 1)[-1]
    buffer = io.BytesIO(raw)
    if suffix == "parquet":
        return pd.read_parquet(buffer)
    if suffix == "csv":
        return pd.read_csv(buffer, low_memory=False)
    if suffix in {"xlsx", "xlsm"}:
        return pd.read_excel(buffer, engine="openpyxl")
    raise ValueError(f"Unsupported file type for {filename}. Use CSV, Parquet, or XLSX.")


def _download(file_id: str, filename: str) -> pd.DataFrame:
    response = requests.get(_drive_download_url(file_id), timeout=300)
    response.raise_for_status()
    if "text/html" in response.headers.get("content-type", ""):
        raise RuntimeError(f"Drive returned an HTML page for {filename}; verify that the file is public and the ID is correct.")
    return _read_frame(response.content, filename)


def _clean_code(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def canonical_week(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA
    text = re.sub(r"\s+", " ", str(value).strip().upper())
    match = re.search(r"(?:BTS\s*)?WK\s*0*(\d+)", text)
    return f"BTS WK{int(match.group(1))}" if match else text


def _normalize(df: pd.DataFrame, table: str) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    if "Code" in out:
        out["Code"] = _clean_code(out["Code"])
    if "Location Code" in out:
        out["Location Code"] = _clean_code(out["Location Code"])
    if "Item Barcode" in out:
        out["Item Barcode"] = out["Item Barcode"].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    if "Date" in out:
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    if "BTS WEEK" in out:
        out["BTS WEEK"] = out["BTS WEEK"].map(canonical_week).astype("string")
    if table == "calendar":
        if "Week" in out and "BTS WEEK" not in out:
            out["BTS WEEK"] = out["Week"].map(canonical_week).astype("string")
        if "Day" in out:
            out["DAY"] = pd.to_numeric(out["Day"], errors="coerce").astype("Int64")
    elif "DAY" in out:
        out["DAY"] = pd.to_numeric(out["DAY"], errors="coerce").astype("Int64")
    return out


def _validate(df: pd.DataFrame, required: set[str], name: str) -> None:
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"{name} is missing required columns: {', '.join(missing)}")


def _location_dimensions(location: pd.DataFrame) -> pd.DataFrame:
    _validate(location, {"Code"}, "Location Master")
    if location["Code"].duplicated().any():
        duplicates = location.loc[location["Code"].duplicated(False), "Code"].dropna().unique()[:5]
        raise ValueError(f"Location Master Code must be unique. Duplicate examples: {', '.join(map(str, duplicates))}")
    # Location Code is intentionally retained only as a reference; Code is the sole join key.
    rename = {column: f"LM {column}" for column in location.columns if column != "Code"}
    return location.rename(columns=rename)


def _attach_dimensions(fact: pd.DataFrame, location: pd.DataFrame, calendar: pd.DataFrame | None = None) -> pd.DataFrame:
    result = fact.merge(_location_dimensions(location), on="Code", how="left", validate="many_to_one")
    if calendar is not None and "Date" in result:
        cal = calendar[[c for c in ("Date", "BTS WEEK", "DAY") if c in calendar]].drop_duplicates("Date")
        cal = cal.rename(columns={"BTS WEEK": "CAL BTS WEEK", "DAY": "CAL DAY"})
        result = result.merge(cal, on="Date", how="left", validate="many_to_one")
        if "BTS WEEK" in result:
            result["BTS WEEK"] = result["CAL BTS WEEK"].fillna(result["BTS WEEK"])
        if "DAY" in result:
            result["DAY"] = result["CAL DAY"].fillna(result["DAY"]).astype("Int64")
    return result


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.div(denominator.replace(0, np.nan))


def _aggregate_sales(df: pd.DataFrame, metrics: list[str]) -> pd.DataFrame:
    _validate(df, set(SALES_KEY + metrics), "Sales")
    work = df.copy()
    for metric in metrics:
        work[metric] = pd.to_numeric(work[metric], errors="coerce").fillna(0)
    dims = [c for c in work.columns if c not in metrics and c not in {"Date", "CAL BTS WEEK", "CAL DAY"}]
    preferred = [c for c in dims if c not in SALES_KEY]
    aggregations: dict[str, Any] = {m: "sum" for m in metrics}
    aggregations.update({c: "first" for c in preferred})
    if "Date" in work.columns:
        aggregations["Date"] = "min"
    return work.groupby(SALES_KEY, dropna=False, observed=True).agg(aggregations).reset_index()


def build_models(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    ty = _attach_dimensions(tables["this_year_sales"], tables["location_master"], tables["calendar"])
    ly = _attach_dimensions(tables["last_year_sales"], tables["location_master"], tables["calendar"])
    ty_agg = _aggregate_sales(ty, ["TY NSQ", "TY NSV", "TY COGS"])
    ly_agg = _aggregate_sales(ly, ["LY NSQ", "LY NSV", "LY COGS"])
    ty_agg = ty_agg.rename(columns={"Date": "TY Date"})
    ly_agg = ly_agg.rename(columns={"Date": "LY Date"})
    ly_dims = [c for c in ly_agg.columns if c not in SALES_KEY + ["LY NSQ", "LY NSV", "LY COGS"]]
    ly_agg = ly_agg.rename(columns={c: f"{c}__LY" for c in ly_dims})
    yoy = ty_agg.merge(ly_agg, on=SALES_KEY, how="outer", validate="one_to_one")
    # Preserve descriptive dimensions for LY-only key rows while preferring TY labels
    # where both sides exist. This does not affect the numeric aggregation grain.
    for ly_column in [c for c in yoy.columns if c.endswith("__LY")]:
        base = ly_column[:-4]
        if base in yoy:
            yoy[base] = yoy[base].combine_first(yoy[ly_column])
        else:
            yoy[base] = yoy[ly_column]
        yoy = yoy.drop(columns=ly_column)
    for metric in ("TY NSQ", "TY NSV", "TY COGS", "LY NSQ", "LY NSV", "LY COGS"):
        yoy[metric] = pd.to_numeric(yoy[metric], errors="coerce").fillna(0)
    yoy["Qty Var"] = yoy["TY NSQ"] - yoy["LY NSQ"]
    yoy["Qty Var %"] = _safe_divide(yoy["Qty Var"], yoy["LY NSQ"])
    yoy["NSV Var"] = yoy["TY NSV"] - yoy["LY NSV"]
    yoy["NSV Var %"] = _safe_divide(yoy["NSV Var"], yoy["LY NSV"])
    yoy["TY Margin %"] = _safe_divide(yoy["TY NSV"] - yoy["TY COGS"], yoy["TY NSV"])
    yoy["LY Margin %"] = _safe_divide(yoy["LY NSV"] - yoy["LY COGS"], yoy["LY NSV"])
    soh = _attach_dimensions(tables["week_on_week_soh"], tables["location_master"])
    return {"YOY Sales": yoy, "SOH": soh}


@st.cache_data(ttl=1800, show_spinner="Loading and joining Drive data…", max_entries=2)
def load_all_data(file_config_json: str, refresh_token: int = 0) -> DataBundle:
    del refresh_token
    import json
    config = json.loads(file_config_json)
    tables: dict[str, pd.DataFrame] = {}
    for key in TABLE_KEYS:
        item = config.get(key, {})
        if not item.get("file_id") or not item.get("filename"):
            raise ValueError(f"Configure file_id and filename for '{key}' in Admin > Data Sources.")
        tables[key] = _normalize(_download(str(item["file_id"]), str(item["filename"])), key)
    return DataBundle(tables=tables, models=build_models(tables))


def apply_admin_row_filters(df: pd.DataFrame, rules: dict[str, bool]) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    if not rules.get("include_dc", True) and "LOC Type" in df:
        mask &= ~df["LOC Type"].astype("string").str.upper().eq("DC")
    brand_col = "LM Store Brand" if "LM Store Brand" in df else "Store Brand"
    if not rules.get("include_non_bbz", True) and brand_col in df:
        mask &= ~df[brand_col].astype("string").str.upper().eq("NON-BBZ")
    status_col = "LM Status" if "LM Status" in df else "Status"
    if not rules.get("include_non_operating", True) and status_col in df:
        mask &= ~df[status_col].astype("string").str.upper().eq("NON-OPERATING")
    return df.loc[mask]
