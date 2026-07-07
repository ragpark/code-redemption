"""
als2ah_codegen.py
=================

ALS -> ActiveHub ISBN join utility.

Reads two CSV sources:
  1. Customer extract (e.g. PROD_Extracted_Establishment_School_Data...).
     This is the base design for the output -- its columns are passed
     through unchanged.
  2. A mapping file (ALS ISBN -> AH ISBN / AH QTY).

For each extract row, the ISBN (Col B) is matched against the mapping's
ALS ISBN (Col C). On a match, the AH ISBN (Col E) and AH QTY (Col H)
from the mapping file are appended as extra columns, alongside the
normalised ALS ISBN used to make the match. Unmatched rows are kept in
the output with the AH_ISBN / AH_QTY columns left blank, so every
extract row remains visible for a visual check.

British English throughout.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import pandas as pd

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

EXTRACT_COLUMNS = [
    "SI_No", "ISBN", "Description", "ExpiryDate",
    "SubOwnerFirstName", "SubOwnerLastName", "SubOwnerEmail",
    "NumberOfLicences", "SubID", "SchoolName",
    "VistaCode", "SchoolType", "PostCode",
]

OUTPUT_COLUMNS = EXTRACT_COLUMNS + ["ALS_ISBN", "AH_ISBN", "AH_QTY"]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _clean_text(v) -> str:
    if v is None:
        return ""
    s = str(v)
    s = s.replace("\xa0", " ")
    return s.strip()


def _normalise_isbn(v) -> str:
    """Return a 13-digit ISBN string, or '' if unrecognisable."""
    if v is None:
        return ""
    s = re.sub(r"\D", "", str(v))
    return s if len(s) == 13 else ""


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# -----------------------------------------------------------------------------
# Loaders
# -----------------------------------------------------------------------------

def _find_header_row(path: Path, marker: str = "ALS ISBN", max_scan: int = 10) -> int:
    """Locate the header row by scanning for a known column name.

    Some exports of the mapping file have a descriptive title on line 1
    before the real header; others start directly with the header. Scan
    the first few lines rather than assuming a fixed offset.
    """
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for i, line in enumerate(f):
            if marker in line:
                return i
            if i >= max_scan:
                break
    return 0


def load_mapping(path: Path) -> Dict[str, dict]:
    """Load the ALS -> AH mapping file.

    Some exports have a descriptive title on line 1 before the real
    header; the header row is located dynamically rather than assumed.
    Returns a dict keyed by normalised ALS ISBN (Col C) with the
    corresponding AH ISBN (Col E) and AH QTY (Col H). Where an ALS ISBN
    appears more than once, the first occurrence wins.
    """
    header_row = _find_header_row(path)
    df = pd.read_csv(
        path,
        skiprows=header_row,
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
    )

    wanted = ["ALS ISBN", "AH ISBN", "AH QTY"]
    missing = [c for c in wanted if c not in df.columns]
    if missing:
        raise ValueError(
            f"Mapping file missing expected columns: {missing}. "
            f"Got: {list(df.columns)}"
        )

    mapping: Dict[str, dict] = {}
    for row in df[wanted].to_dict(orient="records"):
        isbn = _normalise_isbn(row["ALS ISBN"])
        if not isbn or isbn in mapping:
            continue
        mapping[isbn] = {
            "AH_ISBN": _clean_text(row["AH ISBN"]),
            "AH_QTY": _clean_text(row["AH QTY"]),
        }
    return mapping


def load_extract(path: Path) -> pd.DataFrame:
    """Load the customer extract, unchanged aside from whitespace cleanup."""
    df = pd.read_csv(
        path,
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
    )
    missing = [c for c in EXTRACT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Extract file missing expected columns: {missing}. "
            f"Got: {list(df.columns)}"
        )

    df = df[EXTRACT_COLUMNS].copy()
    for c in df.columns:
        df[c] = df[c].map(_clean_text)
    return df


# -----------------------------------------------------------------------------
# Core build
# -----------------------------------------------------------------------------

def build_output(extract_df: pd.DataFrame, mapping: Dict[str, dict]) -> pd.DataFrame:
    """Join the extract against the mapping on ISBN and return the output."""
    rows = []
    for row in extract_df.to_dict(orient="records"):
        als_isbn = _normalise_isbn(row["ISBN"])
        match = mapping.get(als_isbn)
        rows.append({
            **row,
            "ALS_ISBN": als_isbn,
            "AH_ISBN": match["AH_ISBN"] if match else "",
            "AH_QTY": match["AH_QTY"] if match else "",
        })
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


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


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------

def run(
    mapping_path: str | Path,
    extract_path: str | Path,
    out_dir: str | Path = "./out",
) -> dict:
    mapping_path = Path(mapping_path)
    extract_path = Path(extract_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mapping = load_mapping(mapping_path)
    extract_df = load_extract(extract_path)
    out_df = build_output(extract_df, mapping)

    matched = int((out_df["AH_ISBN"] != "").sum())

    output_path = out_dir / f"AH_CodeGen_Output_{_timestamp()}.csv"
    write_csv(out_df, output_path)

    return {
        "output_path": output_path,
        "output_df": out_df,
        "matched_rows": matched,
        "total_rows": len(out_df),
    }


def _cli() -> None:
    p = argparse.ArgumentParser(description="ALS -> AH ISBN join utility")
    p.add_argument("--mapping", required=True)
    p.add_argument("--extract", required=True)
    p.add_argument("--out-dir", default="./out")
    args = p.parse_args()
    result = run(args.mapping, args.extract, args.out_dir)
    print(f"OK  Output : {result['output_path']}")
    print(f"    Matched {result['matched_rows']} of {result['total_rows']} rows")


if __name__ == "__main__":
    _cli()
