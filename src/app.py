"""
FastAPI wrapper for the ALS -> ActiveHub Code Generation Utility.

Endpoints
---------
GET  /            Upload UI (HTML form)
GET  /healthz     Liveness probe (Railway healthcheck)
GET  /version     Package version + Git SHA (from Railway env)
POST /run         Multipart upload -> zipped output pack

British English throughout.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from src import als2ah_codegen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
ALLOW_UK_DEFAULT = os.environ.get("ALLOW_UK_DEFAULT", "false").lower() == "true"

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
        "Prepares a PSG-ready CSV that instructs the code generation "
        "system how many unique ActiveHub access codes to create for "
        "each customer line item when migrating from Active Learn."
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
            "allow_uk_default": ALLOW_UK_DEFAULT,
        },
    )


@app.post("/run")
async def run_pipeline(
    mapping: UploadFile = File(..., description="Mapping CSV (ALS -> AH)"),
    extract: UploadFile = File(..., description="Customer extract CSV"),
    mode: str = Form("EstablishmentIntl"),
    allow_uk: Optional[str] = Form(None),
    strict: Optional[str] = Form(None),
):
    """Run the utility against two uploaded CSVs and return a zip pack."""

    if mode not in {"EstablishmentIntl", "D2C"}:
        raise HTTPException(400, "mode must be 'EstablishmentIntl' or 'D2C'.")

    allow_uk_bool = _checkbox(allow_uk) or ALLOW_UK_DEFAULT
    strict_bool = _checkbox(strict)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log.info(
        "Run start ts=%s mode=%s mapping=%s extract=%s allow_uk=%s strict=%s",
        ts, mode, mapping.filename, extract.filename, allow_uk_bool, strict_bool,
    )

    with tempfile.TemporaryDirectory(prefix="als2ah_") as tmp:
        tmp_path = Path(tmp)
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        in_dir.mkdir()
        out_dir.mkdir()

        mapping_path = in_dir / (mapping.filename or "mapping.csv")
        extract_path = in_dir / (extract.filename or "extract.csv")

        _save_upload(mapping, mapping_path)
        _save_upload(extract, extract_path)

        try:
            result = als2ah_codegen.run(
                mapping_path=str(mapping_path),
                extract_path=str(extract_path),
                mode=mode,
                out_dir=str(out_dir),
                allow_uk=allow_uk_bool,
                strict=strict_bool,
            )
        except ValueError as exc:
            log.warning("Validation error: %s", exc)
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Unhandled error during run")
            raise HTTPException(500, f"Internal error: {exc}") from exc

        stats = result.get("stats", {})
        log.info(
            "Run complete ts=%s output_rows=%s exceptions=%s total_qty=%s",
            ts,
            stats.get("output_rows"),
            stats.get("exception_rows"),
            stats.get("total_quantity"),
        )

        # Build in-memory zip of the three artefacts
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for key in ("output_path", "exceptions_path", "summary_path"):
                p = Path(result[key])
                zf.write(p, arcname=p.name)
        buf.seek(0)

        filename = f"AH_CodeGen_Pack_{ts}.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
