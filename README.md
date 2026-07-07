# ALS &rarr; ActiveHub Code Generation Utility

Prepares a PSG-ready CSV that instructs the code generation system how many
unique ActiveHub (AH) access codes to create for each customer line item, when
migrating international establishment (and D2C) customers from Active Learn
Service (ALS) subscriptions to ActiveHub per-user licence codes.

> British English throughout. Source files are read-only; the utility produces
> three artefacts per run: a request CSV, an exceptions CSV and a plain-text
> summary with SHA256 hashes for audit.

---

## 1. Purpose

Bridge the shift from a *subscription-per-establishment* model to a
*unique-code-per-user-per-book* model. The utility joins a mapping file
(ALS ISBN &rarr; AH ISBN/PPID/Title) against a customer extract and emits a
PSG-ready request pack. It does **not** generate codes &mdash; PSG does that
downstream.

## 2. Features

- Mapping-driven join on normalised 13-digit ISBNs
- Explicit `Quantity` per line (`NumberOfLicences` for establishments, `1` for D2C)
- Exceptions file: every unmapped, ambiguous, or invalid row is captured with a reason
- SHA256 hashes on inputs and outputs, timestamped run summary &mdash; audit-ready
- BTEC International coverage check with a warning if none are found
- UK-exclusion guard: refuses to process a UK-inclusive extract unless overridden
- FastAPI web UI plus a CLI mode

## 3. Architecture

```
   +----------------+       +----------------+
   |  mapping.csv   |       |  extract.csv   |
   +-------+--------+       +--------+-------+
           \                        /
            \_______  als2ah  ____/
                    \ codegen /
             +-------+-------+
             |               |
     request.csv     exceptions.csv     summary.txt
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
    --extract "path/to/PROD_Extracted_Establishment_School_Data(No  UK).csv" \
    --mode EstablishmentIntl \
    --out-dir ./out
```

Parameters:

| Flag           | Values                              | Default            |
| -------------- | ----------------------------------- | ------------------ |
| `--mapping`    | path to mapping CSV                 | *(required)*       |
| `--extract`    | path to customer extract CSV        | *(required)*       |
| `--mode`       | `EstablishmentIntl` &#124; `D2C`    | `EstablishmentIntl`|
| `--out-dir`    | output directory                    | `./out`            |
| `--allow-uk`   | flag                                | off                |
| `--strict`     | flag &mdash; non-zero exit on any exception | off        |

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
7. Open the URL, upload the two CSVs, and download the generated pack.

### Environment variables

| Name                 | Purpose                                              | Default   |
| -------------------- | ---------------------------------------------------- | --------- |
| `MAX_UPLOAD_MB`      | Per-file upload cap                                  | `25`      |
| `ALLOW_UK_DEFAULT`   | Pre-tick the &lsquo;Allow UK&rsquo; box in the UI    | `false`   |
| `PORT`               | Injected by Railway automatically                    | *(auto)*  |
| `RAILWAY_GIT_COMMIT_SHA` | Surfaced by `/version` endpoint if present        | `dev`     |

### Health & version endpoints

- `GET /healthz` &rarr; `{"status":"ok"}` (used by Railway's healthcheck)
- `GET /version` &rarr; version and Git SHA

## 7. Output files

| File                                    | Description |
| --------------------------------------- | ----------- |
| `AH_CodeGen_Request_<mode>_<ts>.csv`    | The PSG-ready request &mdash; columns: `SI_No, SubID, SchoolName, VistaCode, SchoolType, PostCode, SubOwnerFirstName, SubOwnerLastName, SubOwnerEmail, ExpiryDate, ALS_ISBN, ALS_Description, AH_ISBN, AH_PPID, AH_Title, Quantity, Scenario, MappingStatus` |
| `AH_CodeGen_Exceptions_<mode>_<ts>.csv` | Every rejected row with a `Reason` (Unmapped ALS ISBN, Ambiguous mapping, Invalid ISBN, Non-positive licences, Missing SubOwnerEmail) |
| `AH_CodeGen_Summary_<mode>_<ts>.txt`    | Row counts, hashes, total quantity, BTEC coverage warning, top unmapped ISBNs |

## 8. Notes & caveats

- **The utility does not generate access codes.** PSG receives the request pack and produces the codes; uniqueness is enforced there.
- **UK establishments are out of scope** for this migration. The utility refuses to run against an extract whose filename lacks &lsquo;No UK&rsquo; unless `--allow-uk` / the UI checkbox is set. This is a guard, not a filter &mdash; per Claire's clarification, the correct input is the &lsquo;No UK&rsquo; extract.
- **BTEC International** coverage is checked at the end of every run and warned on if zero rows are found (per Joanne's flag during the planning meeting).
- **Auditability**: every run stamps SHA256 hashes on both inputs and both outputs, plus the run timestamp.

## 9. License

MIT &mdash; see [LICENSE](LICENSE).
