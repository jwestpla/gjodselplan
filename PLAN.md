# Gjødselplan for frukt – build plan for Claude Code

> Put this file in the repo root (or rename it to `CLAUDE.md`). Build **one phase at a time**, run the tests, and stop for review after each phase.

---

## 0. Instructions to Claude Code (read first)

1. **Never invent agronomic numbers in code.** Every norm, correction, limit and product value lives in `data/reference/*.csv`. Code only reads them. If a value is missing, show a warning in the UI. Do not hardcode a fallback.
2. Every reference CSV row has a `source` column. A row with `source = ANTATT` (assumed, not verified) must trigger a visible warning in the plan view and in the PDF footer ("Planen bygger på ikke-verifiserte normverdier: …").
3. **UI language: Norwegian bokmål.** Code, identifiers and comments: English.
4. Keep the calculation engine (`gjodsel/calc.py`) **pure**: no tkinter, no DB access. It takes dataclasses in and returns dataclasses out. It must be fully unit-tested.
5. Units are stored internally as **pure elements (N, P, K) in kg/daa**. Oxide conversion happens only at input and display (P = P₂O₅ × 0.4364, K = K₂O × 0.8301).
6. Display numbers with a decimal comma (`10,8`). Accept both `,` and `.` on input.
7. "Rough around the edges" is acceptable: this is an advisor tool. Prioritise correct calculations, traceability and data safety over UI polish.
8. Ask before changing the database schema after Phase 1. Write a migration rather than dropping tables.

---

## 1. Purpose and scope

A desktop tool for **fruit advisors** to make annual fertilizer plans (gjødslingsplan) and fertilizer journals (gjødseljournal) for client enterprises, compliant with **gjødselbrukforskriften (FOR-2025-01-29-115) §§ 26, 27 and 29** (in force for plans from 1 Jan 2026).

**Cultures (fruit only):** eple, pære, søtkirsebær (morell), surkirsebær, plomme.

**Out of scope for now:** berries, field crops, manure/organic fertilizer calculations (only a generic product with N/P content is supported), online integrations.

### Legal checklist the tool must satisfy (§ 26 plan contents, per field)
- a) area in daa
- b) soil sample results (≤ 8 years old)
- c) previous crop (forgrøde)
- d) this year's crop
- e) expected yield per daa
- f) this year's N and P requirement per daa
- g) type of fertilizer expected to be used
- h) planned N and P supply in kg per daa
- plus a **map showing the field layout** (kartskisse)

Other rules:
- A plan is required if the enterprise fertilizes **> 25 daa per year**. Below that, no plan is required, but a **journal is always required**.
- The plan must be ready before the season, and adjusted with a stated reason if conditions change.
- The plan and journal must be kept for **5 years**.
- **§ 27 journal:** field, date, fertilizer type and amount, N and P per field, and total N and P for the enterprise.
- **§ 29 soil samples:** representative, analysed by a lab, sample locations with coordinates.
- **P ceiling** for all fertilizer combined (organic + mineral). It is measured on the enterprise average, with max +10 % per daa on individual fields. Values are in `p_limits.csv`.

---

## 2. Tech stack

- Python 3.11+
- `tkinter` + `ttk` (stdlib). `ttk.Treeview` for tables.
- `sqlite3` (stdlib) for all client and plan data. One file: `data/gjodselplan.db`.
- **Reference data as CSV** in `data/reference/`. These files are editable in Excel and are loaded and validated at startup.
- `reportlab` for PDF export.
- `pytest` for tests.
- Later phase only: `tkintermapview` for maps.
- No other dependencies without asking. Provide `requirements.txt`.

### Why SQLite + CSV
Client and plan data is relational (enterprise → property → field → season → applications), so it goes in SQLite. To keep the raw data easy to see:
- a **"Rådata"** tab shows any table in a Treeview;
- a button exports all tables to CSV (`data/export/<date>/<table>.csv`, UTF-8 with BOM, `;` separator, so Norwegian Excel opens them correctly);
- the README mentions *DB Browser for SQLite* for direct inspection.

---

## 3. Project layout

```
gjodselplan/
  PLAN.md
  README.md
  requirements.txt
  app.py                    # entry point: python app.py
  gjodsel/
    __init__.py
    config.py               # paths, data dir (overridable via env GJODSEL_DATA)
    db.py                   # connection, schema, migrations, backup
    models.py               # dataclasses
    repo.py                 # CRUD functions (SQL lives here only)
    reference.py            # load + validate reference CSVs
    units.py                # oxide/element conversion, number parse/format
    validation.py           # org.nr mod11, postnr, phone, email, kommunenr
    calc.py                 # PURE calculation engine
    planning.py             # orchestration: build plan rows, balances, P-check, legal status
    pdf_export.py
    csv_export.py
    ui/
      main_window.py        # sidebar (enterprise + year) + Notebook tabs
      enterprise_tab.py
      fields_tab.py         # properties + fields
      soil_tab.py
      plan_tab.py
      journal_tab.py
      rawdata_tab.py
      settings_tab.py
      widgets.py            # reusable form/table helpers, decimal-comma entry
  data/
    reference/              # versioned in git
    attachments/            # copied soil reports, map images (relative paths in DB)
    backups/
    export/
  tests/
    test_units.py
    test_validation.py
    test_calc.py
    test_planning.py
    test_reference.py
    fixtures/
```

---

## 4. Reference data (`data/reference/`)

Format: UTF-8, `;` separator. Decimal comma or point are both accepted when reading. Every file has a `source` column. `reference.py` validates headers, types and range coverage (no gaps or overlaps in min/max bands) at startup and shows a clear error dialog if something is wrong.

### 4.1 `cultures.csv`
Base norms are the **midpoint of the NIBIO range**. The NIBIO norms (kg/daa) are: eple N 3–5 / P 1–2 / K 5–7; pære 5–8 / 1–2 / 6–8; søtkirsebær 8–10 / 1–2 / 6–8; plomme 5–7 / 1–2 / 7–9.

```
code;name_nb;n_base;p_base;k_base;yield_basis_kg_daa;n_per_tonne;default_yield_kg_daa;source
eple;Eple;4.0;1.5;6.0;2000;2.0;3500;NIBIO GH tab.7 + NLR Gjødsling i frukthagar 2017
paere;Pære;6.5;1.5;7.0;2000;2.0;3500;NIBIO GH tab.7; yield_basis/n_per_tonne ANTATT
sotkirsebaer;Søtkirsebær (morell);9.0;1.5;7.0;1000;2.0;1500;NIBIO GH tab.7; yield_basis/n_per_tonne ANTATT
surkirsebaer;Surkirsebær;6.0;1.5;7.0;1000;2.0;1500;ANTATT
plomme;Plomme;6.0;1.5;8.0;1500;2.0;1500;NIBIO GH tab.7; yield_basis/n_per_tonne ANTATT
```
Treat a row as `ANTATT` if `source` contains the word ANTATT.

### 4.2 `p_al_correction.csv` (NIBIO GH table 2: potet, frukt og bær)
P-AL in mg P/100 g. Correction is a % of the P base norm. Bands use half-open intervals `[min, max)` so decimal values are covered.
```
pal_min;pal_max;pct;source
0;1.5;100;NIBIO GH tab.2 (2012)
1.5;2.5;75;NIBIO GH tab.2
2.5;3.5;50;NIBIO GH tab.2
3.5;4.5;25;NIBIO GH tab.2
4.5;9.5;0;NIBIO GH tab.2
9.5;13.5;-25;NIBIO GH tab.2
13.5;15.5;-50;NIBIO GH tab.2
15.5;999;-75;NIBIO GH tab.2
```

### 4.3 `k_correction.csv`
NIBIO has **no K table for fruit**. As a default, use NIBIO's table for korn, oljevekster, potet og grønnsaker, marked ANTATT so the advisor replaces or approves it. The table is in long format.

- K-AL bands: `<6.5`, `6.5–10.5`, `10.5–15.5`, `15.5–30.5`, `≥30.5`
- K-HNO₃ bands: `<30`, `30–50`, `50–80`, `80–110`, `110–150`, `150–200`, `>200`

Percentage matrix (rows = K-HNO₃ band, columns = K-AL band, low → high):
```
<30      : 50  25   0    0   0
30-50    : 50  25   0  -10 -50
50-80    : 50  25   0  -25 -50
80-110   : 50   0   0  -25 -50
110-150  : 25   0   0  -25 -50
150-200  :  0 -25 -25  -25 -50
>200     :-15 -40 -40  -50 -60
```
Rule: **if K-HNO₃ is missing (common), use the 50–80 row** and add a warning to the rationale text.

### 4.4 `n_growth_adjustment.csv` (shoot growth class, as in Skifteplan)
```
growth_class;label_nb;n_adj;source
lt20;Nyvekst < 20 cm;1.0;ANTATT
20to40;Nyvekst 20–40 cm;0.0;ANTATT
gt40;Nyvekst > 40 cm;-1.0;ANTATT
```

### 4.5 `age_factors.csv` (years since planting = plan year − planting year)
Years = 0 means the planting year. That year normally uses the "Nyplanting" template (§ 4.9), and the factors only produce the reference requirement.
```
years_min;years_max;stage;n_factor;p_factor;k_factor;apply_yield_adj;source
0;0;planteår;0.75;1.5;0.5;0;ANTATT
1;2;ungtre;0.75;1.0;0.75;0;ANTATT
3;99;bærende;1.0;1.0;1.0;1;ANTATT
```

### 4.6 `n_mold_correction.csv` (NIBIO, general N correction by organic matter)
Applied **only if the setting `apply_mold_correction` is on** (default off for fruit; the advisor decides).
```
mold_min;mold_max;n_adj;source
0;3;2;NIBIO GH N-korreksjon
3;4.5;1;NIBIO GH N-korreksjon
4.5;12.5;0;NIBIO GH N-korreksjon
12.5;20.5;-1;NIBIO GH N-korreksjon
20.5;40.5;-2;NIBIO GH N-korreksjon
40.5;100;-3;NIBIO GH N-korreksjon
```

### 4.7 `p_limits.csv` (kg P/daa, all fertilizer combined, per year)
```
region;year_from;year_to;kg_p_daa;source
standard;2025;2026;3.5;Gjødselbrukforskriften § 20 / Statsforvalteren
standard;2027;2029;2.8;Statsforvalteren (Midt-Gudbrandsdal nyhetsbrev 1/2026)
standard;2030;2032;2.5;Statsforvalteren
standard;2033;2100;2.3;Statsforvalteren
rogaland;2025;2026;3.5;Statsforvaltaren Vestland (Luster 24.03.2026)
rogaland;2027;2029;3.1;Statsforvaltaren Vestland
rogaland;2030;2032;3.0;Statsforvaltaren Vestland
rogaland;2033;2100;2.7;Statsforvaltaren Vestland
```
Also add the setting `p_skew_factor = 1.10` (max per-field skew).

### 4.8 `products.csv` (element %, **verify against product labels before use**)
```
code;name;n;p;k;mg;s;ca;b;price_nok_per_kg;source
kalksalpeter;Kalksalpeter;15.5;0;0;0;0;19;0;;SJEKK ETIKETT
opti_ns_27;OPTI-NS 27-0-0 (4S);27;0;0;0;3.7;0;0;;SJEKK ETIKETT
opti_start_12_23;OPTI-START NP 12-23-0;12;23;0;0;0;0;0;;SJEKK ETIKETT
opti_p_20;OPTI-P 0-20-0;0;20;0;0;0;0;0;;SJEKK ETIKETT
fg_12_4_18;Fullgjødsel 12-4-18 mikro;12;4;18;;;;;;SJEKK ETIKETT
patentkali;Patentkali (Kalimagnesia);0;0;25;6;17;0;0;;SJEKK ETIKETT
multi_k_mg;Multi K-Mg;12;0;35.7;1.2;0;0;0;;SJEKK ETIKETT
```
Treat `SJEKK ETIKETT` (check label) the same way as ANTATT. The UI must have a simple product editor that writes back to this CSV.

### 4.9 `templates.csv` (application templates, based on typical Skifteplan plans)
```
template;stage;order;product_code;kg_per_daa;timing_code;source
Eple bærende standard;bærende;1;opti_ns_27;10;tidlig_vaar;Skifteplan-eksempel NLR 2026
Eple bærende standard;bærende;2;patentkali;40;tidlig_vaar;Skifteplan-eksempel NLR 2026
Eple bærende standard;bærende;3;kalksalpeter;10;for_blomstring;Skifteplan-eksempel NLR 2026
Eple bærende standard;bærende;4;kalksalpeter;10;vekstsesong;Skifteplan-eksempel NLR 2026
Nyplanting;planteår;1;opti_start_12_23;10;etter_planting;Skifteplan-eksempel NLR 2026
Nyplanting;planteår;2;kalksalpeter;10;6u_etter_planting;Skifteplan-eksempel NLR 2026
Plomme bærende;bærende;1;fg_12_4_18;25;tidlig_vaar;Skifteplan-eksempel NLR 2026
Plomme bærende;bærende;2;kalksalpeter;10;for_blomstring;Skifteplan-eksempel NLR 2026
Plomme bærende;bærende;3;kalksalpeter;10;etter_hosting;Skifteplan-eksempel NLR 2026
```

### 4.10 `timings.csv`
```
code;label_nb;season;sort
tidlig_vaar;Tidlig vår;vaar;1
etter_planting;Etter planting;vaar;2
for_blomstring;2 uker før blomstring;vaar;3
etter_blomstring;Etter blomstring;sommer_host;4
6u_etter_planting;6 uker etter planting;sommer_host;5
vekstsesong;I vekstsesongen;sommer_host;6
etter_hosting;Etter høsting;sommer_host;7
```

### 4.11 `soil_optimum.csv` (frukt, NIBIO)
```
parameter;min;max;source
ph;5.5;6.5;NIBIO GH tab.7
p_al;8;12;NLR Gjødsling i frukthagar 2017 (NIBIO: ~10)
k_al;20;30;NIBIO GH tab.7
mg_al;10;12;NIBIO GH tab.7
ca_al;100;200;NIBIO GH tab.7
```

---

## 5. Database schema (SQLite)

Enable `PRAGMA foreign_keys = ON`. Every table has `created_at` and `updated_at` (ISO strings). Keep a `schema_version` table and run migrations at startup.

```sql
CREATE TABLE enterprise (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,                 -- foretaksnavn
  org_nr TEXT UNIQUE,                 -- 9 digits, mod11 check
  contact_first_name TEXT, contact_last_name TEXT,
  street TEXT, postcode TEXT, place TEXT,
  phone TEXT, email TEXT,
  region TEXT NOT NULL DEFAULT 'standard',   -- key into p_limits.csv
  notes TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE property (                -- landbrukseiendom (owned or leased)
  id INTEGER PRIMARY KEY,
  enterprise_id INTEGER NOT NULL REFERENCES enterprise(id) ON DELETE RESTRICT,
  farm_name TEXT,
  kommunenr TEXT NOT NULL, gnr INTEGER NOT NULL, bnr INTEGER NOT NULL,
  fnr INTEGER NOT NULL DEFAULT 0, snr INTEGER NOT NULL DEFAULT 0,
  tenure TEXT NOT NULL DEFAULT 'eid',  -- 'eid' | 'leid'
  map_attachment TEXT,                 -- relative path to map image/PDF (kartskisse)
  notes TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE field (                   -- skifte
  id INTEGER PRIMARY KEY,
  property_id INTEGER NOT NULL REFERENCES property(id) ON DELETE RESTRICT,
  field_no TEXT NOT NULL,              -- unique per enterprise (enforced in repo.py)
  name TEXT NOT NULL,
  area_daa REAL NOT NULL CHECK (area_daa > 0),
  culture_code TEXT NOT NULL,          -- cultures.csv
  variety TEXT, rootstock TEXT,
  planting_year INTEGER,
  row_spacing_m REAL, plant_spacing_m REAL,
  lat REAL, lon REAL,                  -- WGS84 representative point
  polygon_geojson TEXT,                -- optional, later phase
  irrigation TEXT DEFAULT 'ingen',     -- 'ingen' | 'drypp' | 'gjødselvanning'
  cover TEXT DEFAULT 'ingen',          -- 'ingen' | 'tunnel' | 'regntak'
  active INTEGER NOT NULL DEFAULT 1,
  notes TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE soil_sample (
  id INTEGER PRIMARY KEY,
  enterprise_id INTEGER NOT NULL REFERENCES enterprise(id) ON DELETE RESTRICT,
  label TEXT,                          -- name on lab report
  lab TEXT, lab_report_no TEXT,
  sample_date TEXT NOT NULL,           -- ISO date
  depth_from_cm INTEGER, depth_to_cm INTEGER,
  lat REAL, lon REAL,                  -- § 29: sample point coordinates
  ph REAL,
  p_al REAL,                           -- ALWAYS mg P/100 g (convert on entry)
  k_al REAL, mg_al REAL, ca_al REAL,   -- mg/100 g, AL method
  k_hno3 REAL,
  mold_pct REAL,
  soil_type TEXT, clay_class TEXT,
  method TEXT NOT NULL DEFAULT 'AL',   -- 'AL' | 'annet' (non-AL values are stored but NOT used in calc)
  attachment TEXT,                     -- relative path, file copied into data/attachments/
  notes TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE field_soil_sample (
  field_id INTEGER NOT NULL REFERENCES field(id) ON DELETE CASCADE,
  soil_sample_id INTEGER NOT NULL REFERENCES soil_sample(id) ON DELETE CASCADE,
  PRIMARY KEY (field_id, soil_sample_id)
);

CREATE TABLE plan (
  id INTEGER PRIMARY KEY,
  enterprise_id INTEGER NOT NULL REFERENCES enterprise(id) ON DELETE RESTRICT,
  year INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'utkast',   -- 'utkast' | 'ferdig'
  change_reason TEXT,                      -- required for version > 1
  finalized_at TEXT,
  snapshot_json TEXT,                      -- full frozen plan incl. reference rows used
  created_at TEXT, updated_at TEXT,
  UNIQUE (enterprise_id, year, version)
);

CREATE TABLE field_season (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
  field_id INTEGER NOT NULL REFERENCES field(id) ON DELETE RESTRICT,
  fertilized INTEGER NOT NULL DEFAULT 1,
  expected_yield_kg_daa REAL,
  growth_class TEXT,                   -- n_growth_adjustment.csv
  forgrode TEXT,                       -- previous crop (free text; usually same culture)
  soil_sample_id INTEGER REFERENCES soil_sample(id),
  req_n REAL, req_p REAL, req_k REAL,  -- computed
  override_n REAL, override_p REAL, override_k REAL,
  override_reason TEXT,                -- required if any override is set
  rationale TEXT,                      -- generated explanation (Norwegian)
  notes TEXT,
  UNIQUE (plan_id, field_id)
);

CREATE TABLE application (
  id INTEGER PRIMARY KEY,
  field_season_id INTEGER NOT NULL REFERENCES field_season(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                  -- 'planlagt' | 'utfort'
  product_code TEXT NOT NULL,
  kg_per_daa REAL NOT NULL CHECK (kg_per_daa >= 0),
  timing_code TEXT,                    -- timings.csv (planned)
  date TEXT,                           -- required for 'utfort'
  performed_by TEXT, method TEXT,
  note TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT);
-- advisor_name, advisor_company, advisor_phone, advisor_email,
-- apply_mold_correction (0/1), p_skew_factor (1.10), plan_threshold_daa (25), soil_max_age_years (8)
```

Delete rules: deleting an enterprise, property or field that has plans must be refused with a clear message. Offer "deaktiver" instead for fields.

---

## 6. Calculation engine (`calc.py`) – exact behaviour

### Inputs (dataclasses)
- `CultureNorm`
- `FieldInput`: area, culture, planting_year, expected_yield, growth_class
- `SoilInput`: p_al, k_al, k_hno3, mold_pct, ph, sample_date, method
- `PlanYear`
- `RefTables`
- `Settings`

### Output
`Requirement(n, p, k, stage, warnings: list[str], steps: list[Step])`. Each `Step` has `nutrient, label_nb, delta, value_after`. The steps are the audit trail and become the rationale text.

### Algorithm
1. `years = plan_year − planting_year`. If `planting_year` is missing, assume `bærende` and add a warning. Look up the stage and factors in `age_factors.csv`.
2. **Soil validity.** The sample is valid if `method == 'AL'` and its age ≤ `soil_max_age_years`, where age = plan_year − sample year. If invalid or missing, skip the P/K soil corrections, add a warning "Mangler gyldig jordprøve – planen er mangelfull etter § 26", and flag the field.
3. **N**
   - `n = n_base`
   - if the stage applies yield adjustment: `n += (expected_yield − yield_basis)/1000 × n_per_tonne`
   - `n += growth_adj[growth_class]` (0 if no class is set, plus a warning)
   - if the mold-correction setting is on and `mold_pct` is known: `n += mold_adj`
   - `n *= n_factor`
   - clamp at ≥ 0
4. **P**: `p = p_base × (1 + pal_pct/100) × p_factor`, clamped at ≥ 0.
5. **K**: `k = k_base × (1 + k_pct/100) × k_factor`, clamped at ≥ 0. Use the 50–80 K-HNO₃ row if K-HNO₃ is missing (with a warning).
6. Round to 0.1 kg. Each step appends a `Step`.
7. **Soil-status notes** (no effect on numbers): compare pH, P-AL, K-AL, Mg-AL and Ca-AL with `soil_optimum.csv` and add text notes such as "pH 5,8 under optimum 5,5–6,5 – vurder kalking". Note: 5.8 is within range, so it should *not* trigger; write a test for that.
8. If any reference row used has an ANTATT or SJEKK ETIKETT source, add a warning.

### Rationale text (generated, Norwegian)
Example: `N: norm 4,0 + avling 3500 kg (+3,0) + nyvekst 20–40 cm (±0) = 7,0 kg/daa. P: norm 1,5, P-AL 25 (>15) −75 % = 0,4 kg/daa. K: norm 6,0, K-AL 10, K-HNO₃ mangler (antatt 50–80) +25 % = 7,5 kg/daa.`

### Supply calculation (`planning.py`)
- Per application: `nutrient_kg_daa = kg_per_daa × pct/100` for N, P, K, Mg, S, Ca.
- Per field-season: sum the planned applications and, separately, the actual ones.
- Balance = planned − effective requirement, where effective = override if set, otherwise computed.

### Enterprise-level checks
- `fertilized_area = Σ area of field_seasons with fertilized = 1`.
- **Legal status:** "Gjødslingsplan påkrevd (> 25 daa)" or "Ikke påkrevd (≤ 25 daa) – gjødseljournal er likevel påkrevd".
- **P check** using the limit for (region, year):
  - enterprise average `Σ(P_planned × area) / fertilized_area` must be ≤ limit;
  - each field's planned P must be ≤ limit × p_skew_factor.
  - Red warning if breached.
- **Soil checks:** any fertilized field without a valid AL sample is "mangelfull". Also warn when a sample expires within 1 year ("utløper snart").
- **Duplicate check:** `field_no` must be unique within the enterprise.

---

## 7. UI (tkinter)

**Main window**
- Left sidebar: enterprise selector (searchable list) + year spinbox.
- Right: `ttk.Notebook` with the tabs below.
- Status bar: legal status and P-check for the selected enterprise and year.

| Tab | Content |
|---|---|
| **Foretak** | CRUD form + list. Validation: org.nr mod11 (weights 3,2,7,6,5,4,3,2; checksum rules), postnr 4 digits, phone 8 digits (allow +47), basic email check. |
| **Eiendommer og skifter** | Properties list. Button "Åpne i Gårdskart" opens `https://gardskart.nibio.no/search` and copies `knr/gnr/bnr/fnr/snr` to the clipboard. Only add a deep link after the URL format has been verified manually. Attach map file. Fields list with CRUD. "Vis i kart" opens `https://www.google.com/maps?q=<lat>,<lon>` and "Norgeskart" opens norgeskart.no. Show computed trees/daa = 1000 / (row × plant spacing). |
| **Jordprøver** | CRUD. P-AL entry has a unit dropdown (`mg P/100 g` or `mg P₂O₅/100 g`) and converts on save. Method dropdown (AL / annet). "Legg ved rapport" copies the file into `data/attachments/<enterprise_id>/` and stores the relative path. Multi-select fields that the sample covers. Table colours each value against `soil_optimum.csv` (low/ok/high). |
| **Gjødslingsplan** | Year plan for the selected enterprise (see below). |
| **Journal** | Actual applications per field: add, edit, delete. Button "Kopier planlagt → utført" pre-fills from the plan with a chosen date. Summary per field and enterprise totals of N and P (and K). |
| **Rådata** | Dropdown of all tables → read-only Treeview. Button "Eksporter alle til CSV". |
| **Innstillinger** | Advisor info (for the PDF header), toggles and numbers from the `setting` table. "Last referansedata på nytt" button. Product editor. |

### Plan tab layout
- **Top bar:** plan status/version, buttons `Opprett plan`, `Beregn alle`, `Kopier fra fjorår`, `Bruk mal…`, `Ferdigstill`, `Ny versjon…`, `Eksporter PDF`.
- **Fields Treeview:** nr, name, culture, daa, stage, req N/P/K, planned N/P/K, P-status icon, warnings icon.
- **Detail panel for the selected field:**
  - season inputs: fertilized, expected yield (default from culture), growth class, forgrøde, soil sample (dropdown of linked samples, newest valid preselected);
  - requirement breakdown (the `steps`);
  - override fields with a required reason;
  - planned applications table (product, kg/daa, timing, → N/P/K kg/daa, total kg = kg/daa × area);
  - balance row coloured green/amber/red (±10 % tolerance).

### Finalising (`Ferdigstill`)
- Allowed even with warnings, but the warnings are listed and must be confirmed.
- Writes `snapshot_json` (all field-season data, applications, computed values, and the **reference rows used**) and sets status to `ferdig`.
- A finished plan is read-only.
- `Ny versjon…` copies it to version + 1 with a required `change_reason`.
- The journal stays editable regardless of plan status.

### Copy from last year
Copies field_seasons and planned applications from the newest finished (or draft) plan of the previous year. It then recalculates requirements with this year's soil and age data and highlights fields whose stage changed (e.g. planteår → ungtre).

---

## 8. PDF export (`pdf_export.py`, reportlab, A4)

1. **Front page:** enterprise (name, org.nr, address, contact), properties (knr/gnr/bnr), advisor info, year, version, status, date. Legal status line, P-check summary, total fertilized area.
2. **Kartskisse:** embedded map attachment(s) per property. If one is missing, print a clear placeholder "KART MANGLER – påkrevd etter § 26".
3. **Plan per field** (one block per field, like Skifteplan): nr/name/culture/variety/area/stage/forgrøde/expected yield; requirement N/P/K with the rationale text; planned applications (timing, product, kg/daa, total kg); **planned N and P kg/daa in bold** (§ 26 h); notes.
4. **Soil analysis overview:** table of all samples with date, age, values and optimum ranges. Expired samples are marked.
5. **Innkjøpsliste:** Σ kg per product split vår / sommer-høst (from `timings.csv`), with prices where known.
6. **Journal / field form:** per field, planned versus actual (with empty rows if nothing is entered, so it can be filled in by hand).
7. **Footer on every page:** page x/y, plan version, and the "ikke-verifiserte normverdier" warning if any were used.

Second export: **Gjødseljournal (§ 27)** for a year. Per field: date, product, amount, N and P kg/daa and total. Enterprise total N and P.

---

## 9. Data safety
- On startup, copy the DB to `data/backups/gjodselplan_YYYYMMDD_HHMMSS.db` and keep the 20 newest.
- All writes go through `repo.py` inside transactions.
- The data folder holds client personal data, so the README notes it should not be placed in a public or shared sync folder without care.

---

## 10. Phases and acceptance criteria

Stop after each phase, run `pytest`, and summarise what was done and what to review.

**Phase 0 – Skeleton**
Project layout, `config.py`, `db.py` with schema + migration framework + backup, `reference.py` with all CSVs from §4 and their validation, `units.py`, `validation.py`. Empty main window with tabs.
✔ `python app.py` opens. ✔ Tests for units (P₂O₅ → P, decimal comma parsing), org.nr mod11, reference validation (gaps/overlaps detected).

**Phase 1 – Master data + Rådata**
Foretak, Eiendommer og skifter, Rådata tab, CSV export, Gårdskart/maps buttons.
✔ CRUD works. ✔ Delete protection. ✔ Duplicate field_no refused. ✔ CSV export opens correctly in Excel with æøå.

**Phase 2 – Soil samples**
Jordprøver tab, attachments, P₂O₅ conversion, field linking, optimum colouring.
✔ Entering 57 mg P₂O₅/100 g stores 24.9 mg P/100 g.

**Phase 3 – Calculation engine**
`calc.py` + `planning.py` with full tests (see §11). No UI yet.

**Phase 4 – Plan tab**
Create plan, season inputs, calculation, overrides, applications, templates, balances, P-check, legal status, status bar.

**Phase 5 – PDF**
Full plan PDF + innkjøpsliste.

**Phase 6 – Journal**
Journal tab, copy planned → actual, § 27 journal PDF.

**Phase 7 – Lifecycle**
Ferdigstill/snapshot, versions, copy from last year.

**Later (do not start without asking)**
Map view with `tkintermapview` and field polygons; importer for lab reports (Eurofins, Fertilab; note that Fertilab non-AL values must map to `method='annet'`); liming requirement; fertigation split (yearly need over weeks, per-tree doses); export for Landbruksdirektoratet's coming plan system.

---

## 11. Test cases (Phase 3, `tests/test_calc.py`)

Use anonymised fixtures ("Testgård"). Expected values follow the reference CSVs above; if a CSV changes, the tests read the expectation from the same CSV where practical.

1. **Bearing apple, P-AL in optimum, low K-AL, K-HNO₃ missing**
   - Input: apple, 10.8 daa, planted 2017, plan 2026, yield 2000, growth 20to40, P-AL 8, K-AL 10, pH 6.5, AL sample from 2025.
   - Expected: N 4.0; P 1.5 (0 %); K 7.5 (+25 %, HNO₃ row 50–80 assumed, warning present).
2. **High P-AL (Fertilab case)**
   - Input: P-AL entered as 57 mg P₂O₅/100 g → 24.9.
   - Expected: P = 1.5 × 0.25 = 0.4 (rounded).
3. **High yield**
   - Input: apple with yield 3500.
   - Expected: N = 4.0 + 1.5 × 2.0 = 7.0.
4. **Planting year**
   - Input: planting_year = plan year.
   - Expected: stage planteår, the factors applied, and no yield adjustment.
5. **Expired sample**
   - Input: sample 9 years old.
   - Expected: no P/K correction, field flagged mangelfull, warning text present.
6. **Non-AL sample** (`method='annet'`)
   - Expected: treated like a missing sample for the corrections.
7. **Growth class < 20 cm**
   - Expected: N +1.0.
8. **Mold correction on, mold 2.5 %**
   - Expected: N +2.0. With the setting off: no change.
9. **Clamping**
   - Expected: no negative values.
10. **pH 5.8**
    - Expected: no pH note (within 5.5–6.5). pH 5.2 → note present.
11. **P-check, enterprise level**
    - Input: 2 fields, 10 daa each, planned P 3.0 and 2.0, year 2027, standard region (limit 2.8).
    - Expected: average 2.5 → OK; field 1 at 3.0 ≤ 2.8 × 1.10 = 3.08 → OK. Change field 1 to 3.2 → per-field breach.
12. **Legal status**
    - Expected: 25.0 daa → not required; 25.1 → required.
13. **ANTATT warning**
    - Expected: using any ANTATT row adds the warning.

---

## 12. Advisor to-do (outside the code)

- Verify or replace the **ANTATT** values: growth adjustment, age factors, sour cherry norms, yield basis for non-apple cultures, the K table for fruit.
- Verify the product nutrient contents in `products.csv` against current labels.
- Check that the P-limit schedule in `p_limits.csv` matches your region.
- Test the Gårdskart URL format and decide whether to add a deep link.

## Sources for reference values
- NIBIO Gjødslingshåndbok, 7. Frukt og bær: https://www.nibio.no/tema/jord/gjodslingshandbok/gjodslingsnormer/7.frukt-og-baer
- NIBIO P-korreksjon tabell 2 (potet, frukt og bær): https://www.nibio.no/tema/jord/gjodslingshandbok/korreksjonstabeller/fosforbehov--korreksjon-etter-jordanalyser
- NIBIO K-korreksjon (korn, potet, grønnsaker): https://www.nibio.no/tema/jord/gjodslingshandbok/korreksjonstabeller/kalium--korn-oljevekster-potet-og-gronnsaker
- NIBIO N-korreksjon: https://www.nibio.no/tema/jord/gjodslingshandbok/korreksjonstabeller/nitrogen
- NLR/NIBIO, Gjødsling i frukthagar (2017): https://www.nlr.no/files/documents/NLRs/Gjodsling-i-frukthagar_Nibio2017.pdf
- Lovdata, gjødselbrukforskriften kap. 6: https://lovdata.no/forskrift/2025-01-29-115
- Landbruksdirektoratet, kommentarer § 26: https://www.landbruksdirektoratet.no/nb/jordbruk/miljo-og-klima/husdyrgjodsel-og-gjodsling/forskrift-om-lagring-og-bruk-av-gjodsel-mv.-kommentarer-til-regelverk/-26.krav-til-gjodslingsplan
- Statsforvaltaren i Vestland, nye krav 2026: https://www.statsforvalteren.no/vestland/landbruk-og-mat/miljotiltak/gjodsel-og-pressaft/nye-krav-til-gjodslingsplan-og-gjodseljournal-fra-2026/
