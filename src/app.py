"""
FastAPI wrapper for the ALS -> ActiveHub Code Generation Utility.

Endpoints
---------
GET  /            Upload UI (HTML form)
GET  /healthz     Liveness probe (Railway healthcheck)
GET  /version     Package version + Git SHA (from Railway env)
POST /run         Multipart upload -> joined output CSV

British English throughout.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from src import als2ah_codegen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("als2ah")

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(
    title="ALS -> ActiveHub Code Generation Utility",
    description=(
        "Joins a customer extract against the ALS -> ActiveHub ISBN "
        "mapping file, appending the matched AH ISBN and AH QTY to "
        "each row for a visual check."
    ),
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/healthz", response_class=JSONResponse)
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/version", response_class=JSONResponse)
async def version() -> dict:
    return {
        "version": app.version,
        "git_sha": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "dev"),
        "environment": os.environ.get("RAILWAY_ENVIRONMENT", "local"),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "max_upload_mb": MAX_UPLOAD_MB,
        },
    )


@app.post("/run")
async def run_pipeline(
    mapping: UploadFile = File(..., description="Mapping CSV (ALS -> AH)"),
    extract: UploadFile = File(..., description="Customer extract CSV"),
    one_to_many: Optional[str] = Form(None),
):
    """Join the two uploaded CSVs and return the resulting CSV."""

    mapping_type = "one_to_many" if _checkbox(one_to_many) else "one_to_one"

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log.info(
        "Run start ts=%s mapping=%s extract=%s mapping_type=%s",
        ts, mapping.filename, extract.filename, mapping_type,
    )

    tmp = tempfile.TemporaryDirectory(prefix="als2ah_")
    try:
        tmp_path = Path(tmp.name)
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        in_dir.mkdir()
        out_dir.mkdir()

        mapping_path = in_dir / (mapping.filename or "mapping.csv")
        extract_path = in_dir / (extract.filename or "extract.csv")

        _save_upload(mapping, mapping_path)
        _save_upload(extract, extract_path)

        result = als2ah_codegen.run(
            mapping_path=str(mapping_path),
            extract_path=str(extract_path),
            out_dir=str(out_dir),
            mapping_type=mapping_type,
        )
    except ValueError as exc:
        tmp.cleanup()
        log.warning("Validation error: %s", exc)
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        tmp.cleanup()
        raise
    except Exception as exc:  # pragma: no cover - defensive
        tmp.cleanup()
        log.exception("Unhandled error during run")
        raise HTTPException(500, f"Internal error: {exc}") from exc

    log.info(
        "Run complete ts=%s matched=%s total=%s",
        ts, result["matched_rows"], result["total_rows"],
    )

    output_path = Path(result["output_path"])
    return FileResponse(
        output_path,
        media_type="text/csv",
        filename=output_path.name,
        background=BackgroundTask(tmp.cleanup),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _checkbox(v: Optional[str]) -> bool:
    if v is None:
        return False
    return str(v).lower() in {"1", "true", "on", "yes", "y"}


def _save_upload(upload: UploadFile, dest: Path) -> None:
    """Stream an upload to disk while enforcing MAX_UPLOAD_BYTES."""
    total = 0
    with dest.open("wb") as fh:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    413,
                    f"Upload exceeds the {MAX_UPLOAD_MB} MB limit "
                    f"for '{upload.filename}'.",
                )
            fh.write(chunk)
