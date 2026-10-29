# Gjødselplan for frukt

@PLAN.md

## Status
- Phases 0–7 built (the user said "just continue", so they ran back to back).
- Reviewer release (v0.1.0): Windows build (PyInstaller one-folder), demo data, feedback, Referansedata review tab,
  tools/merge_review.py, docs/index.html (GitHub Pages). Tested on Windows by the user.
- Sharing: public repo jwestpla/gjodselplan; pushing a v* tag publishes a Release with Gjodselplan-windows.zip. `.github/workflows/windows-build.yml` does the Windows clean-PATH test.
- The schema is frozen since Phase 1: ask before changing it, and write a migration.
- Not started (PLAN §10 "Later", ask first): map view, lab report import, liming, fertigation split, Ldir export.

## Environment
- Use the project venv: `.venv/bin/python app.py`, `.venv/bin/python -m pytest` (python.org 3.11 + reportlab).
- Homebrew `python3` (3.14) has no `_tkinter`. `/usr/local/bin/python3` (3.11) works but lacks reportlab (PDF tests skip).

## Build
- `build_windows.bat` (Windows) or the GitHub workflow. macOS check: `.venv/bin/python tools/make_demo_db.py &&
  .venv/bin/pyinstaller --noconfirm gjodselplan.spec && .venv/bin/python tools/check_bundle.py`, then
  `env -i HOME=$HOME PATH=/bin GJODSEL_DATA=/tmp/x dist/Gjodselplan/Gjodselplan --selftest`.
- Never bundle anything but data/reference/*.csv and build/demo (check_bundle.py enforces it).

## Architecture
- `repo.py`: all SQL; validates via `validation.py`; finished plans are read-only here (journal entries excepted).
- `calc.py` / `planning.py` / `soil.py`: pure. `plan_service.py`: orchestration (DB + calc).
- `plan_view.PlanView`: JSON-serialisable report model. The plan tab, PDF and snapshot all render it;
  finished plans are shown from `plan.snapshot_json` with the live journal overlaid.

## Decisions made during the build (where PLAN.md was silent or ambiguous)
- Reference CSVs: decimal comma, UTF-8 with BOM (Excel), source text with `;` double-quoted.
- `k_correction.csv` columns: `khno3_min;khno3_max;kal_min;kal_max;pct;use_when_hno3_missing;source`;
  the missing-K-HNO₃ fallback band is a data flag. Open-ended top bands use 999 / 9999.
- `timings.csv` has no `source` column. An empty `source` counts as unverified (like ANTATT / SJEKK ETIKETT).
- The reference dir is always the project's `data/reference/`; `GJODSEL_DATA` only moves client data.
- Schema additions to PLAN §5: CHECK constraints for the plan's own rules, created_at/updated_at defaults
  (also on field_season, field_soil_sample), FK indexes, default settings seeded in migration 1.
- `enterprise.org_nr` is UNIQUE: store NULL, not '', when unknown.
- Rounding is round-half-up (`units.round_half_up`).
- calc: soil-dependent steps (P/K correction, mold N, soil notes) use only a valid AL sample; a planting year
  after the plan year gives no requirement; missing yield uses `default_yield_kg_daa` with a warning.
- Balance colours: within ±10 % = ok (green), below = under (amber), above = over (red).
- Plan defaults: yield from cultures.csv, forgrøde = same culture if planted before the plan year,
  newest valid linked soil sample.
- New version: copies inputs, overrides and planned applications; journal entries move to the new version.
- Copy from last year: copies inputs and planned applications, not overrides; re-picks soil samples.
- A journal without a required plan is a draft plan with no planned applications (no schema change).
- PDF: Helvetica only; characters outside cp1252 are mapped (₃→3, −→-, ≥→>=).
- Packaged data folder: %APPDATA%\Gjodselplan (not Documents: OneDrive often syncs Documents, bad for SQLite and
  client data). Reference CSVs live there too when packaged; first start copies only missing files.
- Feedback e-mail: `config.FEEDBACK_EMAIL` (the user's gmail; not shown on the public index.html).
- Review files (feedback.csv, review_status.csv, reference_changes.csv) live in the data folder, not next to the CSVs.
  review_status has one row per (file, row_key); row keys are codes/stages/bands (`reference_review.row_key`).
  Band edits rename the key; save_session moves the review entry. Edited rows get status "Endret" automatically.
- "X av Y vurdert": Y = rows with ANTATT/SJEKK ETIKETT source or with any review entry.
