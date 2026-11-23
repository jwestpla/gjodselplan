# Gjødselplan for frukt

@PLAN.md

## Status
- Phases 0–7 built (the user said "just continue", so they ran back to back).
- Reviewer release (v0.1.0): Windows build (PyInstaller one-folder), demo data, feedback, Referansedata review tab,
  tools/merge_review.py, docs/index.html (GitHub Pages). Tested on Windows by the user.
- Sharing: public repo jwestpla/gjodselplan; pushing a v* tag publishes a Release with Gjodselplan-windows.zip. `.github/workflows/windows-build.yml` does the Windows clean-PATH test.
- The schema is frozen since Phase 1: ask before changing it, and write a migration.
  Migration 2 (v0.1.2, approved by the user): soil_sample.origin 'norsk' | 'utenlandsk'.
- Generated farm map (eiendomskart) built on the user's request (v0.1.4, see decisions). Not started (PLAN §10
  "Later", ask first): lab report import, liming, fertigation split, Ldir export.

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
- Soil sample origin: UI labels "Standardpakke 1" ('norsk') and "Utvidet analyse" ('utenlandsk'), `models.SOIL_ORIGIN_LABELS`.
  Origin is separate from method (AL/annet). Lab report PDFs of samples linked to fields in the plan are appended to the
  plan PDF (pypdf, approved dependency): required for Utvidet analyse, optional for Standardpakke 1. One report
  (often one PDF for the whole farm) can serve many samples (`repo.share_soil_report`, "Gjenbruk" in the dialog);
  it is appended once (deduplicated by file content). Non-PDF reports are listed as not attached.
- Kartskisse PDFs are inserted as pages right after the property's Kartskisse heading (pypdf merge after build);
  the "Side x av y" footer counts inserted and appended pages. Unreadable/encrypted map PDFs print "KART MANGLER".
- Plan PDF: each field in "Plan per skifte", Jordanalyser and Innkjøpsliste on their own pages; journal form one page per field
  (15 empty rows). § 27 journal: one page per field, then a "Sum for foretaket" page with a per-field summary.
- One soil sample → many fields via field_soil_sample links (no duplicating results). After linking, the soil tab
  offers to switch draft-plan seasons that have no valid / an older sample (`plan_service.sample_plan_updates`).
- Coordinates: Google Maps in the browser + paste (`geo.parse_coordinates`, `ui/coordinates.py`), no API key, no
  embedded map. Short links (maps.app.goo.gl) can't be read offline.
- Gjødslingsplan tab (v0.1.1) is laid out for a 1366×768 laptop at 125 % (Tk sees ~1093×614): context-dependent
  header buttons, one-line status strip with collapsible warnings, field detail in sub-tabs (Gjødsling / Sesong /
  Beregning), sash sized to the detail's needs. Treeview rowheight = font linespace + 6 (window-wide).
- Eiendomskart (generated farm map, `farm_map.py` pure + network, `map_service.py`, `ui/map_dialog.py`):
  boundary from Kartverket Eiendom API (`geokoding`, omrade=true, fetched in EPSG:4258), background Kartverket
  topo/topograatone WMS + NIBIO `ar5forvaltning2` (open; NOT `cgi-bin/ar5`, which is Norge digitalt begrenset).
  No open aerial photo exists (Norge i bilder needs GeoID). WGS84→UTM is pure Python (`geo.wgs84_to_utm`, Krüger,
  tested against Kartverket's transformation API); zone 32/33/35 by longitude. Cache per property in
  `<data>/kart/<eid>/eiendom_<pid>/` (bakgrunn.jpg + kart.json); points are placed at draw time (vector overlay in
  Tk and PDF). `build_view`/`finalize` take `data_dir=` (None = no maps); finalize copies the background to
  `attachments/<eid>/planer/`. A generated map satisfies the kartskisse warning; the uploaded NIBIO map follows it
  in the PDF. Network runs in a worker thread (`run_in_background`); offline gives a message. macOS python.org has
  no CA store: `farm_map._context` falls back to /etc/ssl/cert.pem. `--karttest` (workflow, continue-on-error).
- Flyfoto from the uploaded gårdskart (v0.1.5, `gardskart.py` pure, `map_service.prepare/save_photo_map`, map
  window): NIBIO gk_enkel (Mapfish Print, no georef) has a JPEG photo + an RGBA overlay (SMask) in the same frame
  (read from the content stream `cm`, never hard-coded); boundary pixels are RGB (255,153,153). Scale is read
  from "Målestokk 1: N" (varies, e.g. 1:1000). Position by Hough voting (boundary samples × pink pixels), local
  refinement with a graded score (on line > ±1 px > ±2 px), zone 32/33/35 + ±1 % scale by score. Confident when
  score ≥ 0.90, ≥ 150 m boundary in frame and the best rival > 10 m away is ≥ 0.08 lower; else drag-to-align.
  Real files: 4020-178/1/0 1:4000 → UTM32, score 0.99; NIBIO prints this area in UTM32. Stored as
  `flyfoto.jpg`/`flyfoto.json` next to the generated map; `valg.json` picks the active one (`load_cached`).
  A new upload removes the photo map. Photo date is not in the PDF; credit names Norge i bilder via NIBIO.
  Tests use synthetic gårdskart PDFs (`tests/conftest.make_gardskart_pdf`); never commit real ones.
  `run_in_background` disables the cyclic GC while the worker runs (Tk objects must die on the main thread).
- P: `p_base` = 2,0 kg/daa for all cultures = annual consumption (advisor's decision, v0.1.5; source text in
  cultures.csv); rationale says "P: årlig forbruk 2,0 …". Still corrected by P-AL and age.
- Plan PDF shows K as numbers only: no K part of «Beregning», no K-table notes (K-HNO₃/k_correction.csv/
  K-korreksjon) and k_correction.csv left out of the «ikke-verifiserte normverdier» lists (filtered at render
  time in pdf_export, so old snapshots too). The app still shows them. K-AL soil notes stay.
- Reference CSV updates reach existing installs: `config.update_reference_files` replaces an installed file only
  if it is byte-identical to an earlier shipped version (`gjodsel/reference_versions.SHIPPED`); the old copy goes
  to `reference/tidligere/`, and the main window shows a note. Edited files are never overwritten. When changing a
  file in data/reference/, add the replaced version's sha256 to SHIPPED (test_config checks the git history).

