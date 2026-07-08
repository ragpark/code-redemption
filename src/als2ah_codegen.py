"""
als2ah_codegen.py
=================

ALS -> ActiveHub ISBN join utility.

Reads two CSV sources:
  1. Customer extract. Two layouts are supported (see "extract_type"
     below). This is the base design for the output -- its columns are
     passed through unchanged.
  2. A mapping file (ALS ISBN -> AH ISBN / AH Title / AH QTY).

For each extract row, the ISBN is matched against the mapping's ALS
ISBN. On a match, the AH ISBN, AH Title and AH QTY from the mapping
file are appended as extra columns, alongside the normalised ALS ISBN
used to make the match. Unmatched rows are kept in the output with the
AH_ISBN / AH_Title / AH_QTY columns left blank, so every extract row
remains visible for a visual check -- one output row per extract row
(or, for a 1-to-many mapping, one output row per matched AH ISBN).

Two extract layouts are supported:
  - "establishment" (default): the PROD_Extracted_Establishment_School
    export, keyed on ISBN (Col B).
  - "d2c": the direct-to-consumer redeemed-access-code export (one row
    per customer/access code), keyed on ISBN (Col A).

Two mapping file layouts are supported:
  - "one_to_one" (default): each ALS ISBN maps to a single AH ISBN.
  - "one_to_many": each ALS ISBN maps to several AH ISBNs. The ALS ISBN
    / ALS title are only populated on the first row of a group; the
    following rows (blank ALS ISBN) add further AH ISBNs to that same
    group. A matched extract row is exploded into one output row per
    AH ISBN in its group.

British English throughout.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

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

D2C_EXTRACT_COLUMNS = [
    "ISBN", "Description", "AccessCodes", "RedemmedDate", "ExpiryDate",
    "FirstName", "LastName", "Email", "UserName", "UserType",
    "LastLogin", "UserStatus", "SchID", "SchoolName",
    "VistaCode", "SchoolType", "PostCode",
]

EXTRA_COLUMNS = ["ALS_ISBN", "AH_ISBN", "AH_Title", "AH_QTY"]

MAPPING_TYPES = ("one_to_one", "one_to_many")
EXTRACT_TYPES = ("establishment", "d2c")


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
    corresponding AH ISBN (Col E), AH Title (Col G) and AH QTY (Col H).
    Where an ALS ISBN appears more than once, the first occurrence wins.
    """
    header_row = _find_header_row(path)
    df = pd.read_csv(
        path,
        skiprows=header_row,
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
    )

    wanted = ["ALS ISBN", "AH ISBN", "AH Title", "AH QTY"]
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
            "AH_Title": _clean_text(row["AH Title"]),
            "AH_QTY": _clean_text(row["AH QTY"]),
        }
    return mapping


def load_mapping_many(path: Path) -> Dict[str, List[dict]]:
    """Load a 1-to-many ALS -> AH mapping file.

    Same columns as the 1-to-1 mapping, but ALS ISBN is only populated
    on the first row of each group; subsequent rows (blank ALS ISBN)
    add further AH ISBNs to that same group -- forward-fill the ALS
    ISBN down through the blanks to reconstruct the grouping. Returns a
    dict keyed by normalised ALS ISBN, mapping to an ordered list of
    {AH_ISBN, AH_Title, AH_QTY} entries (mapping-file order preserved).
    """
    header_row = _find_header_row(path)
    df = pd.read_csv(
        path,
        skiprows=header_row,
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
    )

    wanted = ["ALS ISBN", "AH ISBN", "AH Title", "AH QTY"]
    missing = [c for c in wanted if c not in df.columns]
    if missing:
        raise ValueError(
            f"Mapping file missing expected columns: {missing}. "
            f"Got: {list(df.columns)}"
        )

    mapping: Dict[str, List[dict]] = defaultdict(list)
    current_isbn = ""
    for row in df[wanted].to_dict(orient="records"):
        raw_isbn = _clean_text(row["ALS ISBN"])
        if raw_isbn:
            current_isbn = _normalise_isbn(raw_isbn)

        ah_isbn = _normalise_isbn(row["AH ISBN"])
        if not current_isbn or not ah_isbn:
            continue

        mapping[current_isbn].append({
            "AH_ISBN": ah_isbn,
            "AH_Title": _clean_text(row["AH Title"]),
            "AH_QTY": _clean_text(row["AH QTY"]),
        })
    return dict(mapping)


def _load_extract(path: Path, columns: List[str]) -> pd.DataFrame:
    """Load a customer extract, unchanged aside from whitespace cleanup."""
    df = pd.read_csv(
        path,
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
    )
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(
            f"Extract file missing expected columns: {missing}. "
            f"Got: {list(df.columns)}"
        )

    df = df[columns].copy()
    for c in df.columns:
        df[c] = df[c].map(_clean_text)
    return df


def load_extract(path: Path) -> pd.DataFrame:
    """Load the establishment customer extract (ISBN in Col B)."""
    return _load_extract(path, EXTRACT_COLUMNS)


def load_extract_d2c(path: Path) -> pd.DataFrame:
    """Load the D2C redeemed-access-code extract (ISBN in Col A).

    One row per customer/access code rather than per subscription line;
    the join and output logic are otherwise identical to the
    establishment extract.
    """
    return _load_extract(path, D2C_EXTRACT_COLUMNS)


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
            "AH_Title": match["AH_Title"] if match else "",
            "AH_QTY": match["AH_QTY"] if match else "",
        })
    return pd.DataFrame(rows, columns=list(extract_df.columns) + EXTRA_COLUMNS)


def build_output_many(extract_df: pd.DataFrame, mapping: Dict[str, List[dict]]) -> pd.DataFrame:
    """Join the extract against a 1-to-many mapping and return the output.

    A matched extract row is exploded into one output row per AH ISBN
    in its ALS ISBN's group; an unmatched row is kept once with
    AH_ISBN / AH_QTY left blank, same as the 1-to-1 join.
    """
    rows = []
    for row in extract_df.to_dict(orient="records"):
        als_isbn = _normalise_isbn(row["ISBN"])
        matches = mapping.get(als_isbn)
        if matches:
            for m in matches:
                rows.append({
                    **row,
                    "ALS_ISBN": als_isbn,
                    "AH_ISBN": m["AH_ISBN"],
                    "AH_Title": m["AH_Title"],
                    "AH_QTY": m["AH_QTY"],
                })
        else:
            rows.append({
                **row,
                "ALS_ISBN": als_isbn,
                "AH_ISBN": "",
                "AH_Title": "",
                "AH_QTY": "",
            })
    return pd.DataFrame(rows, columns=list(extract_df.columns) + EXTRA_COLUMNS)


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
    mapping_type: str = "one_to_one",
    extract_type: str = "establishment",
) -> dict:
    if mapping_type not in MAPPING_TYPES:
        raise ValueError(f"mapping_type must be one of {MAPPING_TYPES}")
    if extract_type not in EXTRACT_TYPES:
        raise ValueError(f"extract_type must be one of {EXTRACT_TYPES}")

    mapping_path = Path(mapping_path)
    extract_path = Path(extract_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    extract_df = (
        load_extract_d2c(extract_path) if extract_type == "d2c"
        else load_extract(extract_path)
    )
    if mapping_type == "one_to_many":
        mapping_many = load_mapping_many(mapping_path)
        out_df = build_output_many(extract_df, mapping_many)
    else:
        mapping_one = load_mapping(mapping_path)
        out_df = build_output(extract_df, mapping_one)

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
    p.add_argument("--mapping-type", choices=MAPPING_TYPES, default="one_to_one")
    p.add_argument("--extract-type", choices=EXTRACT_TYPES, default="establishment")
    args = p.parse_args()
    result = run(
        args.mapping, args.extract, args.out_dir,
        args.mapping_type, args.extract_type,
    )
    print(f"OK  Output : {result['output_path']}")
    print(f"    Matched {result['matched_rows']} of {result['total_rows']} rows")


if __name__ == "__main__":
    _cli()
