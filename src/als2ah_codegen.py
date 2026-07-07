"""
als2ah_codegen.py
=================

ALS -> ActiveHub Access Code Migration Preparation Utility.

Reads two CSV sources:
  1. A mapping file (ALS ISBN -> AH ISBN / PPID / Title / Qty).
  2. A customer extract (International establishment subscriptions,
     UK already excluded upstream).

Produces three artefacts in --out-dir:
  * AH_CodeGen_Request_<mode>_<timestamp>.csv     (PSG-ready request)
  * AH_CodeGen_Exceptions_<mode>_<timestamp>.csv  (unmapped / invalid rows)
  * AH_CodeGen_Summary_<mode>_<timestamp>.txt     (audit summary)

British English throughout.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

OUTPUT_COLUMNS = [
    "SI_No", "SubID", "SchoolName", "VistaCode", "SchoolType", "PostCode",
    "SubOwnerFirstName", "SubOwnerLastName", "SubOwnerEmail",
    "ExpiryDate", "ALS_ISBN", "ALS_Description",
    "AH_ISBN", "AH_PPID", "AH_Title",
    "Quantity", "Scenario", "MappingStatus",
]

EXCEPTION_COLUMNS = [
    "SI_No", "SubID", "SchoolName", "SubOwnerEmail",
    "ALS_ISBN", "ALS_Description", "NumberOfLicences",
    "Reason", "Detail",
]

VALID_MODES = ("EstablishmentIntl", "D2C")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _clean_text(v) -> str:
    if v is None:
        return ""
    s = str(v)
    # Normalise NBSP, decode HTML entities, strip
    s = s.replace("\xa0", " ")
    s = html.unescape(s)
    return s.strip()


def _normalise_isbn(v) -> str:
    """Return a 13-digit ISBN string, or '' if unrecognisable."""
    if v is None:
        return ""
    s = re.sub(r"\D", "", str(v))
    return s if len(s) == 13 else ""


def _to_int(v, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return default


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# -----------------------------------------------------------------------------
# Loaders
# -----------------------------------------------------------------------------

def load_mapping(path: Path) -> Tuple[pd.DataFrame, Dict[str, dict], Dict[str, List[dict]]]:
    """Load the ALS -> AH mapping file.

    The file has a descriptive title on line 1 and the real header on line 2.
    Returns:
        - the cleaned dataframe
        - dict of unambiguous mappings, keyed by ALS ISBN
        - dict of ambiguous mappings, keyed by ALS ISBN (list of candidate rows)
    """
    df = pd.read_csv(
        path,
        skiprows=1,          # skip the descriptive first row
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
    )

    wanted = [
        "UK or International owned product", "List",
        "ALS ISBN", "ALS title",
        "AH ISBN", "AH PPID", "AH Title", "AH QTY",
    ]
    missing = [c for c in wanted if c not in df.columns]
    if missing:
        raise ValueError(
            f"Mapping file missing expected columns: {missing}. "
            f"Got: {list(df.columns)}"
        )

    df = df[wanted].copy()
    df.columns = [
        "Region", "List",
        "ALS_ISBN_raw", "ALS_Title",
        "AH_ISBN_raw", "AH_PPID", "AH_Title", "AH_QTY",
    ]
    for c in df.columns:
        df[c] = df[c].map(_clean_text)

    df["ALS_ISBN"] = df["ALS_ISBN_raw"].map(_normalise_isbn)
    df["AH_ISBN"] = df["AH_ISBN_raw"].map(_normalise_isbn)

    # Drop rows that cannot be used as lookup targets
    df = df[(df["ALS_ISBN"] != "") & (df["AH_ISBN"] != "")].reset_index(drop=True)

    # Build lookup structures
    grouped: Dict[str, List[dict]] = defaultdict(list)
    for row in df.to_dict(orient="records"):
        grouped[row["ALS_ISBN"]].append(row)

    unambiguous: Dict[str, dict] = {}
    ambiguous: Dict[str, List[dict]] = {}
    for isbn, rows in grouped.items():
        distinct_ah = {r["AH_ISBN"] for r in rows}
        if len(distinct_ah) == 1:
            unambiguous[isbn] = rows[0]
        else:
            ambiguous[isbn] = rows

    return df, unambiguous, ambiguous


def load_extract(path: Path) -> pd.DataFrame:
    """Load the establishment / D2C customer extract."""
    df = pd.read_csv(
        path,
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
    )
    expected = {
        "SI_No", "ISBN", "Description", "ExpiryDate",
        "SubOwnerFirstName", "SubOwnerLastName", "SubOwnerEmail",
        "NumberOfLicences", "SubID", "SchoolName",
        "VistaCode", "SchoolType", "PostCode",
    }
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(
            f"Extract file missing expected columns: {sorted(missing)}. "
            f"Got: {list(df.columns)}"
        )

    for c in df.columns:
        df[c] = df[c].map(_clean_text)

    df["ISBN_norm"] = df["ISBN"].map(_normalise_isbn)
    df["NumberOfLicences_int"] = df["NumberOfLicences"].map(_to_int)
    return df


# -----------------------------------------------------------------------------
# Core build
# -----------------------------------------------------------------------------

def build_output(
    extract_df: pd.DataFrame,
    unambig: Dict[str, dict],
    ambig: Dict[str, List[dict]],
    mode: str,
    allow_uk: bool,
    extract_filename: str,
) -> Tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Build the output and exceptions dataframes plus a stats dict."""
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}")

    normalised_name = re.sub(r"\s+", " ", extract_filename.lower())
    if not allow_uk and ("no uk" not in normalised_name):
        raise ValueError(
            "Extract filename does not contain 'No UK'. "
            "Pass allow_uk=True to override."
        )

    output_rows: List[dict] = []
    exception_rows: List[dict] = []
    unmapped_counter: Counter = Counter()
    unmapped_descriptions: Dict[str, Counter] = defaultdict(Counter)

    for row in extract_df.to_dict(orient="records"):
        base_exc = {
            "SI_No": row.get("SI_No", ""),
            "SubID": row.get("SubID", ""),
            "SchoolName": row.get("SchoolName", ""),
            "SubOwnerEmail": row.get("SubOwnerEmail", ""),
            "ALS_ISBN": row.get("ISBN_norm", "") or row.get("ISBN", ""),
            "ALS_Description": row.get("Description", ""),
            "NumberOfLicences": row.get("NumberOfLicences", ""),
        }

        # Validation
        if not row["ISBN_norm"]:
            exception_rows.append({**base_exc, "Reason": "Invalid ISBN",
                                    "Detail": f"raw='{row.get('ISBN','')}'"})
            continue
        if row["NumberOfLicences_int"] <= 0:
            exception_rows.append({**base_exc, "Reason": "Non-positive licences",
                                    "Detail": f"NumberOfLicences={row.get('NumberOfLicences','')}"})
            continue
        if not row["SubOwnerEmail"]:
            exception_rows.append({**base_exc, "Reason": "Missing SubOwnerEmail",
                                    "Detail": ""})
            continue

        isbn = row["ISBN_norm"]

        if isbn in ambig:
            candidates = "; ".join(sorted({r["AH_ISBN"] for r in ambig[isbn]}))
            exception_rows.append({**base_exc, "Reason": "Ambiguous mapping",
                                    "Detail": f"Candidates: {candidates}"})
            continue

        if isbn not in unambig:
            unmapped_counter[isbn] += 1
            unmapped_descriptions[isbn][row.get("Description", "")] += 1
            exception_rows.append({**base_exc, "Reason": "Unmapped ALS ISBN",
                                    "Detail": ""})
            continue

        m = unambig[isbn]
        qty = row["NumberOfLicences_int"] if mode == "EstablishmentIntl" else 1

        output_rows.append({
            "SI_No": row["SI_No"],
            "SubID": row["SubID"],
            "SchoolName": row["SchoolName"],
            "VistaCode": row["VistaCode"],
            "SchoolType": row["SchoolType"],
            "PostCode": row["PostCode"],
            "SubOwnerFirstName": row["SubOwnerFirstName"],
            "SubOwnerLastName": row["SubOwnerLastName"],
            "SubOwnerEmail": row["SubOwnerEmail"],
            "ExpiryDate": row["ExpiryDate"],
            "ALS_ISBN": isbn,
            "ALS_Description": row["Description"],
            "AH_ISBN": m["AH_ISBN"],
            "AH_PPID": m["AH_PPID"],
            "AH_Title": m["AH_Title"],
            "Quantity": qty,
            "Scenario": mode,
            "MappingStatus": "Mapped",
        })

    out_df = pd.DataFrame(output_rows, columns=OUTPUT_COLUMNS)
    exc_df = pd.DataFrame(exception_rows, columns=EXCEPTION_COLUMNS)

    stats = {
        "extract_total": len(extract_df),
        "output_rows": len(out_df),
        "exceptions_rows": len(exc_df),
        "mapped": int((out_df["MappingStatus"] == "Mapped").sum()) if len(out_df) else 0,
        "unmapped_isbn_counts": unmapped_counter,
        "unmapped_descriptions": unmapped_descriptions,
        "ambiguous_count": int((exc_df["Reason"] == "Ambiguous mapping").sum()) if len(exc_df) else 0,
        "invalid_isbn_count": int((exc_df["Reason"] == "Invalid ISBN").sum()) if len(exc_df) else 0,
        "nonpos_licences_count": int((exc_df["Reason"] == "Non-positive licences").sum()) if len(exc_df) else 0,
        "missing_email_count": int((exc_df["Reason"] == "Missing SubOwnerEmail").sum()) if len(exc_df) else 0,
        "unmapped_count": int((exc_df["Reason"] == "Unmapped ALS ISBN").sum()) if len(exc_df) else 0,
        "total_quantity": int(out_df["Quantity"].sum()) if len(out_df) else 0,
        "unique_schools": int(out_df["SchoolName"].nunique()) if len(out_df) else 0,
        "unique_subids": int(out_df["SubID"].nunique()) if len(out_df) else 0,
    }

    # BTEC coverage
    if len(out_df):
        btec_mask = (
            out_df["ALS_Description"].str.contains("btec", case=False, na=False)
            | out_df["AH_Title"].str.contains("btec", case=False, na=False)
        )
        stats["btec_row_count"] = int(btec_mask.sum())
        stats["btec_quantity"] = int(out_df.loc[btec_mask, "Quantity"].sum())
    else:
        stats["btec_row_count"] = 0
        stats["btec_quantity"] = 0

    return out_df, exc_df, stats


# -----------------------------------------------------------------------------
# Writers
# -----------------------------------------------------------------------------

def write_csv(df: pd.DataFrame, path: Path) -> None:
    """Write a CSV UTF-8-sig with CRLF line endings, RFC 4180 quoting."""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        writer.writerow(df.columns.tolist())
        for row in df.itertuples(index=False, name=None):
            writer.writerow(["" if v is None else v for v in row])


def write_summary(
    path: Path,
    *,
    mode: str,
    mapping_path: Path, extract_path: Path,
    output_path: Path, exceptions_path: Path,
    stats: dict,
) -> None:
    lines: List[str] = []
    ap = lines.append

    ap("ALS -> ActiveHub Access Code Migration Utility -- Run Summary")
    ap("=" * 68)
    ap(f"Run timestamp (UTC) : {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    ap(f"Mode                : {mode}")
    ap("")

    ap("Input files")
    ap("-" * 68)
    for label, p in (("Mapping ", mapping_path), ("Extract ", extract_path)):
        size = p.stat().st_size
        ap(f"{label} : {p.name}")
        ap(f"           size  = {size:,} bytes")
        ap(f"           sha256= {_sha256(p)}")
    ap("")

    ap("Row counts")
    ap("-" * 68)
    ap(f"Extract rows read              : {stats['extract_total']:>6}")
    ap(f"Output (mapped) rows           : {stats['output_rows']:>6}")
    ap(f"Exception rows                 : {stats['exceptions_rows']:>6}")
    ap(f"  - Unmapped ALS ISBN          : {stats['unmapped_count']:>6}")
    ap(f"  - Ambiguous mapping          : {stats['ambiguous_count']:>6}")
    ap(f"  - Invalid ISBN               : {stats['invalid_isbn_count']:>6}")
    ap(f"  - Non-positive licences      : {stats['nonpos_licences_count']:>6}")
    ap(f"  - Missing SubOwnerEmail      : {stats['missing_email_count']:>6}")
    ap("")

    ap("Code generation request")
    ap("-" * 68)
    ap(f"Total unique codes to generate : {stats['total_quantity']:,}")
    ap(f"Unique schools                 : {stats['unique_schools']:,}")
    ap(f"Unique subscription IDs        : {stats['unique_subids']:,}")
    ap("")

    ap("BTEC International coverage check")
    ap("-" * 68)
    ap(f"Output rows referencing BTEC   : {stats['btec_row_count']:,}")
    ap(f"BTEC code quantity requested   : {stats['btec_quantity']:,}")
    if stats["btec_row_count"] == 0:
        ap("WARNING: No BTEC International rows found in the output.")
        ap("         Per the migration planning meeting, BTEC International")
        ap("         was flagged as potentially missing from the establishment")
        ap("         extract. Claire to confirm with Anchor whether BTEC")
        ap("         International ISBNs were included in the data pull.")
    ap("")

    ap("Top-10 unmapped ALS ISBNs (candidates for mapping updates)")
    ap("-" * 68)
    unmapped = stats["unmapped_isbn_counts"].most_common(10)
    if not unmapped:
        ap("(none)")
    else:
        for isbn, count in unmapped:
            desc_counter = stats["unmapped_descriptions"].get(isbn, Counter())
            top_desc = desc_counter.most_common(1)
            desc = top_desc[0][0] if top_desc else ""
            ap(f"  {isbn}  x {count:>3}   {desc[:60]}")
    ap("")

    ap("Output artefacts")
    ap("-" * 68)
    ap(f"Request CSV    : {output_path.name}")
    ap(f"                 size  = {output_path.stat().st_size:,} bytes")
    ap(f"                 sha256= {_sha256(output_path)}")
    ap(f"Exceptions CSV : {exceptions_path.name}")
    ap(f"                 size  = {exceptions_path.stat().st_size:,} bytes")
    ap(f"                 sha256= {_sha256(exceptions_path)}")
    ap("")
    ap("End of summary.")

    path.write_text("\n".join(lines), encoding="utf-8")


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------

def run(
    mapping_path: str | Path,
    extract_path: str | Path,
    mode: str = "EstablishmentIntl",
    out_dir: str | Path = "./out",
    allow_uk: bool = False,
    strict: bool = False,
) -> dict:
    mapping_path = Path(mapping_path)
    extract_path = Path(extract_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}")

    _, unambig, ambig = load_mapping(mapping_path)
    extract_df = load_extract(extract_path)

    out_df, exc_df, stats = build_output(
        extract_df, unambig, ambig, mode, allow_uk, extract_path.name,
    )

    if strict and len(exc_df):
        raise RuntimeError(f"Strict mode: {len(exc_df)} exception rows encountered.")

    ts = _timestamp()
    output_path = out_dir / f"AH_CodeGen_Request_{mode}_{ts}.csv"
    exceptions_path = out_dir / f"AH_CodeGen_Exceptions_{mode}_{ts}.csv"
    summary_path = out_dir / f"AH_CodeGen_Summary_{mode}_{ts}.txt"

    write_csv(out_df, output_path)
    write_csv(exc_df, exceptions_path)
    write_summary(
        summary_path,
        mode=mode,
        mapping_path=mapping_path, extract_path=extract_path,
        output_path=output_path, exceptions_path=exceptions_path,
        stats=stats,
    )

    return {
        "output_path": output_path,
        "exceptions_path": exceptions_path,
        "summary_path": summary_path,
        "stats": stats,
        "output_df": out_df,
        "exceptions_df": exc_df,
    }


def _cli() -> None:
    p = argparse.ArgumentParser(description="ALS -> AH code generation prep utility")
    p.add_argument("--mapping", required=True)
    p.add_argument("--extract", required=True)
    p.add_argument("--mode", choices=list(VALID_MODES), default="EstablishmentIntl")
    p.add_argument("--out-dir", default="./out")
    p.add_argument("--allow-uk", action="store_true")
    p.add_argument("--strict", action="store_true")
    args = p.parse_args()
    result = run(
        args.mapping, args.extract, args.mode, args.out_dir,
        args.allow_uk, args.strict,
    )
    print(f"OK  Output     : {result['output_path']}")
    print(f"OK  Exceptions : {result['exceptions_path']}")
    print(f"OK  Summary    : {result['summary_path']}")


if __name__ == "__main__":
    _cli()
