"""Smoke tests for the ALS -> AH utility and its FastAPI wrapper."""
from fastapi.testclient import TestClient

from src import als2ah_codegen
from src.app import app


client = TestClient(app)


def test_module_has_run():
    assert callable(getattr(als2ah_codegen, "run", None))


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
