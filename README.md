# ALS &rarr; ActiveHub Code Generation Utility

Joins a customer extract against the ALS &rarr; ActiveHub (AH) ISBN mapping
file, so each line item shows which AH ISBN and AH QTY it corresponds to
when migrating customers from Active Learn Service (ALS) subscriptions to
ActiveHub per-user licence codes.

> British English throughout. Source files are read-only; the utility
> produces a single output CSV per run.

---

## 1. Purpose

The utility takes the customer extract as its base &mdash; every column and
every row is kept as-is &mdash; and extends it with the result of matching
each row's `ISBN` against the mapping file's `ALS ISBN`. On a match, the
mapping's `AH ISBN` and `AH QTY` are appended; unmatched rows are kept in
the output with those columns left blank, so they stay visible for a visual
check rather than being silently dropped.

## 2. Features

- Mapping-driven join: extract `ISBN` &rarr; mapping `ALS ISBN`, both
  normalised to 13-digit ISBNs before comparing
- Every extract row is preserved in the output, matched or not
- Appends `ALS_ISBN` (the normalised join key), `AH_ISBN` and `AH_QTY` as
  new columns
- FastAPI web UI plus a CLI mode

## 3. Architecture

```
   +----------------+       +----------------+
   |  mapping.csv   |       |  extract.csv   |
   +-------+--------+       +--------+-------+
           \                        /
            \_______  als2ah  ____/
                    \ codegen /
                       |
                  output.csv
```

## 4. Quick start (local)

```bash
git clone https://github.com/<you>/als2ah-codegen.git
cd als2ah-codegen

python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn src.app:app --reload
# Open http://localhost:8000
```

## 5. Running the CLI directly

```bash
python -m src.als2ah_codegen \
    --mapping "path/to/Redeemed access code mapping ALS to AH(D2C 1 to 1).csv" \
    --extract "path/to/PROD_Extracted_Establishment_School_Data.csv" \
    --out-dir ./out
```

Parameters:

| Flag           | Values                              | Default            |
| -------------- | ----------------------------------- | ------------------ |
| `--mapping`    | path to mapping CSV                 | *(required)*       |
| `--extract`    | path to customer extract CSV        | *(required)*       |
| `--out-dir`    | output directory                    | `./out`            |

## 6. Deploying to Railway

1. **Push this repo to GitHub** &mdash; the layout is Railway-ready.
2. In [Railway](https://railway.com), click **New Project &rarr; Deploy from GitHub repo** and select the repo.
3. Railway auto-detects Python via `runtime.txt` and `railway.toml`; it builds with Nixpacks and starts:
   ```
   uvicorn src.app:app --host 0.0.0.0 --port $PORT
   ```
4. Wait for the first build (~90 seconds). The `/healthz` probe should turn green.
5. Under **Settings &rarr; Networking**, click **Generate Domain** to obtain a public URL.
6. Optional: add environment variables under **Variables** (see below).
7. Open the URL, upload the two CSVs, and download the joined CSV.

### Environment variables

| Name                 | Purpose                                              | Default   |
| -------------------- | ---------------------------------------------------- | --------- |
| `MAX_UPLOAD_MB`      | Per-file upload cap                                  | `25`      |
| `PORT`               | Injected by Railway automatically                    | *(auto)*  |
| `RAILWAY_GIT_COMMIT_SHA` | Surfaced by `/version` endpoint if present        | `dev`     |

### Health & version endpoints

- `GET /healthz` &rarr; `{"status":"ok"}` (used by Railway's healthcheck)
- `GET /version` &rarr; version and Git SHA

## 7. Output file

| File                              | Description |
| ---------------------------------- | ----------- |
| `AH_CodeGen_Output_<ts>.csv`      | The extract's original columns (`SI_No, ISBN, Description, ExpiryDate, SubOwnerFirstName, SubOwnerLastName, SubOwnerEmail, NumberOfLicences, SubID, SchoolName, VistaCode, SchoolType, PostCode`) plus `ALS_ISBN, AH_ISBN, AH_QTY` |

Rows with no mapping match keep `ALS_ISBN` (the normalised extract ISBN)
but leave `AH_ISBN` and `AH_QTY` blank.

## 8. Notes & caveats

- **The utility does not generate access codes** and does not decide how
  many codes to request &mdash; it is a join/lookup step only.
- Where the mapping file lists more than one AH ISBN for the same ALS
  ISBN, the first occurrence in the mapping file is used.

## 9. License

MIT &mdash; see [LICENSE](LICENSE).
