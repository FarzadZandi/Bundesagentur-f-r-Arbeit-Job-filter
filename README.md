# BA Job Filter

A polite, resumable command-line pipeline for collecting jobs from Germany's Bundesagentur für Arbeit (BA), enriching advertisements from linked employer pages, and ranking them against a configurable set of career profiles.

The project is designed for a high-recall job-search workflow: obviously unsuitable roles are removed, strong matches are prioritized, and ambiguous opportunities are kept for human review instead of being silently discarded.

## What it does

- Reads one or more public BA Jobsuche search URLs.
- Uses the public BA JSON endpoint when available and falls back to rendered HTML.
- Fetches each advertisement and optionally enriches it from the employer's linked job page.
- Normalizes German and English text for consistent matching.
- Applies employment-format, profession, language, relevance, and academic-role gates.
- Scores ten independently configurable CV families (B01-B10).
- Exports separate Excel workbooks for full-time and other accepted formats.
- Produces a compact, phone-friendly text digest of priority jobs.
- Stores seen advertisements in SQLite so interrupted or repeated runs are resumable.
- Caches HTTP responses and applies per-host throttling, randomized pauses, bounded retries, and `robots.txt` checks.

## Supported job families

| Code | Focus |
| --- | --- |
| B01 | Corporate and institute research |
| B02 | Strategy and business development |
| B03 | Incubation, founder centers, and technology transfer |
| B04 | Startups, ventures, and commercialization |
| B05 | Forecasting and planning |
| B06 | Data analytics and business intelligence |
| B07 | Market intelligence and market research |
| B08 | Consulting and analytics consulting |
| B09 | Digital and web analytics |
| B10 | Digital transformation and digital strategy |

The keyword dictionary, weights, gates, flags, score bands, and family tie-breakers live in [`keywords_jobseeking_student_v1.json`](keywords_jobseeking_student_v1.json). The Python package contains the reusable collection, scoring, persistence, and export logic.

## Decision pipeline

Each advertisement flows through these stages:

1. **Employment gate** — accepts structured full-time, part-time, working-student, internship, graduate-program, and trainee formats. Ausbildung, dual-study, minijob, temporary-help, freelance, and unknown formats are rejected.
2. **Hard and conditional exclusions** — removes explicit non-target professions and commercial roles while preserving analytical or strategic exceptions.
3. **Academic routing** — sends explicit doctoral roles and uncertain academic-research positions to a separate review sheet.
4. **Language routing** — routes relevant jobs with C1/C2, native-level, fluent, *verhandlungssicher*, or equivalent strong German requirements to `DE REQUIRED`.
5. **Relevance gate** — admits a strong B01-B10 match directly; a weak family match needs analytical-task evidence or a substantially English-language advertisement.
6. **Scoring** — scores all ten families but adds only the highest family contribution to the final score, avoiding inflation from overlapping terms.
7. **Flags and output routing** — records constraints such as enrollment, mandatory-internship status, remaining study time, work authorization, duration, or weekly hours without turning them into legal conclusions.

The final routes are:

| Route | Meaning |
| --- | --- |
| `PRIORITY` | Strong match; default score is 12 or higher. |
| `REVIEW` | Relevant, uncertain, or lower-scoring result worth manual review. |
| `DE REQUIRED` | Relevant role with a strong German-language requirement. |
| `ACADEMISCHE` | Explicit doctoral role or uncertain academic-research position. |
| `DROP` | Definite exclusion; retained in the workbook for auditability. |

## Requirements

- Python 3.11 or newer
- Internet access for live searches
- A contact email in the local configuration, used in polite request identification

No browser automation, login, CAPTCHA bypass, proxy rotation, or access-control evasion is used.

## Installation

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item config.example.yaml config.yaml
Copy-Item urls.example.txt urls.txt
```

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp config.example.yaml config.yaml
cp urls.example.txt urls.txt
```

Edit `config.yaml` and replace the placeholder contact address before the first network run. Add one public BA search URL per non-comment line in `urls.txt`.

## Usage

After installation, either use the `jobfilter` console command or run `python -m jobfilter`.

### Run one search

```powershell
jobfilter run --url "https://www.arbeitsagentur.de/jobsuche/suche?..." --max-ads 20
```

`--url` may be repeated to process several searches in one run.

### Run all configured searches

```powershell
jobfilter run --urls-file urls.txt
```

### Limit pages or advertisements

```powershell
jobfilter run --urls-file urls.txt --max-pages 3 --max-ads 100
```

`0` means unlimited for both options. The default configuration processes up to 500 previously unseen advertisements per run. Running the same command again skips saved advertisements and continues with the next batch.

### Keep only recent listings

```powershell
jobfilter run --urls-file urls.txt --since 14d
```

The accepted syntax is a whole number followed by `d`, such as `3d` or `28d`.

### Choose a different output directory

```powershell
jobfilter run --urls-file urls.txt --out .\my-results
```

### Score saved text without network access

```powershell
jobfilter score-text --title "Working Student Data Analytics" --employment-type "Werkstudent" --file .\ad.txt
```

The command prints a JSON explanation including the band, score, family contributions, matched terms, flags, and decision reason.

### Explain one live BA advertisement

```powershell
jobfilter explain --url "https://www.arbeitsagentur.de/jobsuche/jobdetail/REFERENCE"
```

This fetches the advertisement, attempts external-page enrichment, and prints the job plus its scoring explanation as JSON.

### Use another configuration file

The global `--config` option must appear before the subcommand:

```powershell
jobfilter --config .\config.local.yaml run --urls-file urls.txt
```

## Outputs and resumability

A successful run writes:

- `out/jobs_vollzeit_YYYY-MM-DD.xlsx` for ordinary full-time roles.
- `out/jobs_other_YYYY-MM-DD.xlsx` for working-student, internship, graduate/trainee, part-time, and other accepted early-career formats.
- `out/jobs_YYYY-MM-DD.txt` containing the combined `PRIORITY` digest.
- `seen.sqlite` containing advertisement identities and a run log.
- Cached responses below `cache/`, grouped by host.

Both workbooks contain `PRIORITY`, `REVIEW`, `DE Required`, `Academische`, and `DROPPED` sheets. Same-day runs receive `_2`, `_3`, and later suffixes rather than overwriting earlier results.

An advertisement is recognized using a canonicalized URL and a title/employer identity hash. The store is updated during processing, so the next run can continue after an interruption. Back up or remove `seen.sqlite` only when you intentionally want to revisit previously processed jobs.

These runtime files are excluded from Git because they may be large, transient, or contain scraped personal/contact information.

## Configuration reference

Start from [`config.example.yaml`](config.example.yaml).

| Setting | Purpose |
| --- | --- |
| `contact_email` | Real contact address used to identify polite requests. |
| `keywords_path` | Path to the scoring dictionary. |
| `cache_dir` | HTTP response-cache directory. |
| `database_path` | SQLite seen-history and run-log path. |
| `output_dir` | Workbook and digest destination. |
| `min_delay`, `max_delay` | Randomized per-host delay range in seconds. The minimum cannot be below 2.5. |
| `long_pause_every` | Number of network requests between longer pauses. |
| `long_pause_min`, `long_pause_max` | Long network-pause range in seconds. |
| `position_delay_min`, `position_delay_max` | Pause between advertisements, including cached ones. |
| `position_batch_size_min`, `position_batch_size_max` | Random range used to choose a longer position-batch boundary. |
| `position_batch_pause_min`, `position_batch_pause_max` | Pause range at a position-batch boundary. |
| `max_retries` | Retry count, constrained to 0-3. |
| `request_timeout` | Per-request timeout in seconds. |
| `max_ads` | Maximum new advertisements per run; `0` is unlimited. |
| `max_pages` | Maximum listing pages per search; `0` is unlimited. |
| `arbeitsagentur_use_api` | Prefer the public BA JSON endpoint before HTML fallback. |
| `thresholds.priority` | Minimum score for `PRIORITY`. |
| `thresholds.review` | Lower review threshold used by scoring logic. |
| `user_agents` | User-agent strings rotated across requests. |

Relative paths in the configuration are resolved from the directory containing that configuration file, not necessarily from the current working directory.

## Tuning the scoring dictionary

Most policy changes belong in `keywords_jobseeking_student_v1.json`, not in Python. The dictionary defines:

- accepted and rejected employment evidence;
- ten CV-family term sets and weights;
- hard and conditional title exclusions;
- analytical tasks and English-language evidence;
- German-language requirement patterns;
- bonuses, penalties, flags, score bands, and tie-breakers.

After editing it, validate both syntax and behavior:

```powershell
python -m json.tool keywords_jobseeking_student_v1.json > $null
pytest
```

Calibration should be based on reviewed false positives and false negatives. Broad weak terms increase `REVIEW` volume; overly narrow family terms reduce recall. Keep representative cases in `tests/fixtures/student_scoring_cases.json` so tuning remains reproducible.

## Project structure

```text
jobfilter/
  __main__.py               CLI commands and argument parsing
  config.py                 configuration loading and validation
  fetch.py                  polite HTTP client, cache, retries, robots checks
  runner.py                 orchestration, enrichment, routing, run statistics
  scoring.py                normalization, gates, family scoring, explanations
  export.py                 Excel workbooks and text digest
  store.py                  SQLite history and URL/identity deduplication
  adapters/
    base.py                 shared job-ad data structures and adapter interface
    arbeitsagentur.py       BA JSON/HTML listing and detail parser
tests/                      unit tests and offline HTML/JSON fixtures
keywords_jobseeking_student_v1.json
config.example.yaml
urls.example.txt
pyproject.toml
```

## Development and testing

Install the development dependency and run the complete offline suite:

```powershell
python -m pip install -e ".[dev]"
pytest
```

The tests cover URL canonicalization and persistence, BA API and HTML parsing, external-description extraction, pacing, scoring gates, all B01-B10 families, output generation, and resumable-run behavior. Network access is not required for the test suite.

## Responsible use and limitations

- Respect the BA website's terms, `robots.txt`, and applicable law.
- Keep the configured delays; do not use this project to generate excessive traffic.
- Search-page markup and partner sites can change, so parsers may need maintenance.
- Keyword scoring is heuristic and can produce false positives or false negatives.
- Language, citizenship, work-authorization, enrollment, and similar flags are prompts for human review, not legal advice or eligibility decisions.
- Generated spreadsheets may contain job-advertisement contact details; do not publish the `out/`, `cache/`, archive, or SQLite files.

## License

No open-source license has been granted yet. Unless a license file is added, the repository's contents remain under the copyright holder's default rights.
