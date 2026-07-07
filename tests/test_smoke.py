"""Smoke tests for the ALS -> AH utility and its FastAPI wrapper."""
from fastapi.testclient import TestClient

from src import als2ah_codegen
from src.app import app


client = TestClient(app)

MAPPING_CSV = (
    "Descriptive title row,,,,,,,\n"
    "UK or International owned product,List,ALS ISBN,ALS title,AH ISBN,AH PPID,AH Title,AH QTY\n"
    "International,Science,9781292103273,Some ALS Title,9781292999999,PPID1,Some AH Title,1\n"
)

EXTRACT_CSV = (
    "SI_No,ISBN,Description,ExpiryDate,SubOwnerFirstName,SubOwnerLastName,"
    "SubOwnerEmail,NumberOfLicences,SubID,SchoolName,VistaCode,SchoolType,PostCode\n"
    "1,9781292103273,Matched Book,2027-01-01,Jane,Doe,jane@example.com,10,100,"
    "Test School,V1,Establiment,\n"
    "2,9780000000000,Unmatched Book,2027-01-01,John,Doe,john@example.com,5,101,"
    "Test School,V1,Establiment,\n"
)


def test_module_has_run():
    assert callable(getattr(als2ah_codegen, "run", None))


def test_build_output_matches_and_keeps_unmatched(tmp_path):
    mapping_path = tmp_path / "mapping.csv"
    extract_path = tmp_path / "extract.csv"
    mapping_path.write_text(MAPPING_CSV, encoding="utf-8")
    extract_path.write_text(EXTRACT_CSV, encoding="utf-8")

    result = als2ah_codegen.run(mapping_path, extract_path, tmp_path / "out")

    assert result["total_rows"] == 2
    assert result["matched_rows"] == 1

    out_df = result["output_df"]
    matched = out_df[out_df["ISBN"] == "9781292103273"].iloc[0]
    assert matched["ALS_ISBN"] == "9781292103273"
    assert matched["AH_ISBN"] == "9781292999999"
    assert matched["AH_QTY"] == "1"

    unmatched = out_df[out_df["ISBN"] == "9780000000000"].iloc[0]
    assert unmatched["ALS_ISBN"] == "9780000000000"
    assert unmatched["AH_ISBN"] == ""
    assert unmatched["AH_QTY"] == ""


def test_build_output_mapping_without_title_row(tmp_path):
    """Some mapping exports start directly with the header, no title row."""
    mapping_path = tmp_path / "mapping.csv"
    extract_path = tmp_path / "extract.csv"
    mapping_path.write_text(
        MAPPING_CSV.split("\n", 1)[1],  # drop the descriptive title row
        encoding="utf-8",
    )
    extract_path.write_text(EXTRACT_CSV, encoding="utf-8")

    result = als2ah_codegen.run(mapping_path, extract_path, tmp_path / "out")

    assert result["matched_rows"] == 1


def test_healthz_ok():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_index_html():
    r = client.get("/")
    assert r.status_code == 200
    assert "ALS" in r.text


def test_version_endpoint():
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body and "git_sha" in body
