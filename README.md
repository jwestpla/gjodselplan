# Gjødselplan for frukt

Desktop tool for fruit advisors: annual fertilizer plans (gjødslingsplan) and
fertilizer journals (gjødseljournal) per gjødselbrukforskriften §§ 26, 27 and 29.
See [PLAN.md](PLAN.md) for the full specification.

## Setup

Needs Python 3.11+ **with tkinter**. The python.org installer includes it;
Homebrew Python needs `brew install python-tk@3.x` (matching your version).

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # reportlab (PDF) and pytest
.venv/bin/python app.py                       # start the app
.venv/bin/python -m pytest                    # run the tests
```

## Windows build (for reviewers)

The reviewer gets a folder with `Gjodselplan.exe`; no Python needed on their PC.

On a Windows PC with Python 3.11 from python.org:

```bat
build_windows.bat
```

The script makes a build venv (`.venv-build`), installs the pinned versions in
`requirements-build.txt`, runs the tests, builds the demo database
(`tools/make_demo_db.py`), runs PyInstaller with `gjodselplan.spec`, checks the
bundle (`tools/check_bundle.py`: demo data only, all reference files), runs
`Gjodselplan.exe --selftest` in an empty data folder, and zips the result to
`dist/Gjodselplan-<version>-windows.zip`. Put that zip next to `site/index.html`
on the website (update the file name in the download link when the version changes).

No Windows PC? Push the repo to GitHub and run the workflow **Windows-bygg**
(`.github/workflows/windows-build.yml`) from the Actions tab. It builds on
`windows-latest`, removes Python from PATH, unpacks the zip and runs the
selftest twice (first start in `%APPDATA%\Gjodselplan`, then a normal start).
The zip is attached to the run as an artifact.

Details:

- One-folder build (not one-file): faster start, no unpacking to `%TEMP%`,
  fewer antivirus false alarms. The exe is not code-signed, so SmartScreen warns
  on first start ("Mer info" → "Kjør likevel"; explained on the download page).
- Packaged, the data folder is `%APPDATA%\Gjodselplan` (never next to the exe).
  First start copies the bundled reference CSVs there (only missing files, so a
  reviewer's edits survive an update) and installs the demo database if there is
  no database yet.
- `Gjodselplan.exe --selftest` checks an installation without user input and
  writes `selftest.log` in the data folder; exit code 0 = OK.
- Errors are logged to `gjodselplan.log` in the data folder (a windowed exe has
  no console) and shown in a dialog.
- The version is `gjodsel.__version__`; it is shown in the title bar and
  written with each feedback entry. The feedback address is
  `config.FEEDBACK_EMAIL`.

## Review round-trip

1. The reviewer reads the plan for the demo enterprise "Testgård", writes feedback
   (*Gi tilbakemelding*, stored in `feedback.csv`) and reviews the norms in the
   **Referansedata** tab: per row *Godkjent / Endret / Uenig* + comment
   (`review_status.csv`), or edits cells directly. Edits are validated with the
   same rules as at startup (no gaps/overlaps), logged in
   `reference_changes.csv`, and draft plans are recalculated at once.
2. *Lag gjennomgangspakke* writes `Gjennomgang_<navn>_<dato>.zip` to Documents
   (changed reference CSVs, review status, change log, feedback).
3. Merge it into the repo:

```sh
.venv/bin/python tools/merge_review.py Gjennomgang_Kari_2026-11-02.zip          # show diff and comments
.venv/bin/python tools/merge_review.py Gjennomgang_Kari_2026-11-02.zip --apply  # choose per file / per change
```

The merge is validated before anything is written to `data/reference/`.
Review `git diff` and run the tests before committing.

## Working with the app

1. **Foretak** – register the enterprise (org.nr. is checked with mod11).
2. **Eiendommer og skifter** – properties (with kartskisse, required by § 26) and
   fields. "Åpne i Gårdskart" copies `knr/gnr/bnr/fnr/snr` to the clipboard.
3. **Jordprøver** – soil samples, linked to fields. P-AL can be entered as
   mg P₂O₅/100 g; it is stored as mg P/100 g. Samples need coordinates (§ 29).
4. **Gjødslingsplan** – choose the year in the sidebar and press *Opprett plan*
   (or *Kopier fra fjorår*). Per field: season inputs, the requirement with
   its full calculation, overrides (reason required), planned fertilizing
   (*Bruk mal…* applies a template from `templates.csv`). The status bar shows
   whether a plan is legally required (> 25 daa) and the P check.
   *Ferdigstill* locks the plan and stores a snapshot; changes then need
   *Ny versjon…* with a reason. *Eksporter PDF* writes the full plan.
5. **Journal** – what was actually spread (§ 27). Always editable, also after
   the plan is finished. Enterprises at ≤ 25 daa need no plan but do need a
   journal: *Opprett journal* sets up the year without planned fertilizing.
6. **Referansedata** – review and edit the reference CSVs (see above).
7. **Innstillinger** – advisor details for the PDF, calculation settings,
   product editor (writes `products.csv`), and reload of the reference data.

The **Gi tilbakemelding** / **Send tilbakemelding** buttons (bottom left, and
in the Hjelp menu) are for reviewers.

## Data and privacy

Everything the app writes lives in `data/` when run from source, in
`%APPDATA%\Gjodselplan` in the Windows build, or in the folder given by the
environment variable `GJODSEL_DATA`:

| Path | Content |
|---|---|
| `gjodselplan.db` | All client, plan and journal data (SQLite) |
| `attachments/` | Copied soil reports and map sketches |
| `backups/` | Automatic copy of the database at every start (20 newest kept) |
| `export/` | CSV and PDF exports |
| `reference/` | Reference CSVs (Windows build only; from source they stay in the project) |
| `feedback.csv`, `review_status.csv`, `reference_changes.csv` | Reviewer feedback and review state |
| `gjodselplan.log` | Error log |

**This is client personal data** (names, addresses, phone numbers, farm data).
Do not put the data folder in a public or shared sync folder (Dropbox,
OneDrive, a shared drive) without thinking about who can read it. It is
excluded from git by `.gitignore`. Plans and journals must be kept for 5 years;
finished plans and plans in use cannot be deleted in the app.

### Looking at the raw data

- The **Rådata** tab shows any table.
- **Eksporter alle til CSV** writes every table to `data/export/<date>/` as
  UTF-8 with BOM and `;` separators, so Norwegian Excel opens it directly.
- [DB Browser for SQLite](https://sqlitebrowser.org) opens `gjodselplan.db`
  directly. Close the app first if you plan to edit.

## Reference data (`data/reference/*.csv`)

All agronomic numbers (norms, corrections, P limits, products, templates) live
in these files, not in the code. They are versioned in git and always read from
the project folder, even if `GJODSEL_DATA` points elsewhere. After editing them
in Excel, press *Last referansedata på nytt* in Innstillinger.

Format rules (checked at startup and on reload; errors are listed in a dialog):

- UTF-8 with BOM, `;` as separator. In Excel use **Lagre som → CSV UTF-8**.
- Decimal comma (`6,5`) or point (`6.5`) both work. The files use decimal comma
  because Norwegian Excel turns `6.5` into a date (6. mai).
- Text containing `;` must be in double quotes (`"NIBIO tab.7; ANTATT"`).
- Every row has a `source`. A source containing **ANTATT** or **SJEKK ETIKETT**
  (or an empty source) marks the row as unverified; plans built on such rows
  show "Planen bygger på ikke-verifiserte normverdier: …" in the plan view and
  in the PDF footer.
- Band tables (`*_min;*_max`) are half-open `[min, max)` and must cover the
  range from 0 without gaps or overlaps. Year/age ranges are inclusive integers.

`k_correction.csv` is the K-AL × K-HNO₃ matrix in long format, one row per
cell. The column `use_when_hno3_missing` = 1 marks the K-HNO₃ band used when a
soil sample has no K-HNO₃ value (exactly one band must be marked).

The calculation tests use a frozen copy in `tests/fixtures/reference/`, so
editing `data/reference/` does not break them.

Before using the tool for real plans, go through the advisor to-do list in
[PLAN.md §12](PLAN.md#12-advisor-to-do-outside-the-code).

## PDF notes

The PDF uses the built-in Helvetica font, so no font files are needed.
Characters it cannot print are written in plain form: K-HNO₃ → K-HNO3,
≥ → >=, the Unicode minus → -. Map sketches are embedded when they are
PNG/JPG; a PDF map is referred to as a separate attachment.
