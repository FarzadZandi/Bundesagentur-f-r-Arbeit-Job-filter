# Bundesagentur für Arbeit B01-B10 Job Filter

This Bundesagentur für Arbeit (BA) job-search project finds positions across Germany that fit one of the ten defined CV families. It covers full-time jobs and non-full-time early-career formats such as Werkstudent, Praktikum, graduate programs, and trainee roles. It has its own keyword dictionary, cache, output directory, SQLite seen-history, and run log.

Default nationwide BA search URL:

`https://www.arbeitsagentur.de/jobsuche/suche?suchbereich=jobs&angebotsart=1;34&arbeitszeit=vz;tz&pav=true&zeitarbeit=true&wo=Deutschland`

## Decision pipeline

Text is normalized once for matching while original German characters are preserved for output. The employment gate first checks structured BA employment metadata, then the title, and only falls back to strong body phrases. Full-time and part-time employment, Werkstudent, Praktikum, graduate programs, and trainee roles can proceed to category scoring. Ausbildung, Minijob, Aushilfe, and unrecognized formats do not pass.

Explicit doctoral-candidate positions are excluded before scoring. University affiliation and university research-staff titles are not exclusions: those jobs can pass when they satisfy a B01-B10 category and all other filters.

German C1/C2, native-level, fluent, verhandlungssicher, and “sehr gute Deutschkenntnisse” wording sends an otherwise relevant B01-B10 job to `DE Required`. Einkauf, Verkäufer/seller, and sales/Vertrieb titles remain definite exclusions. Marketing jobs centered on customer conversations, outreach, acquisition, promotion, or similar talking duties drop unless they contain marketing-research/analytics evidence.

Wrong-profession terms are otherwise conditional title exclusions. HR is maintained: HR strategy, workforce planning, organizational development, and people analytics can qualify. Industry wording is never a blanket exclusion.

All ten CV families are scored independently:

- B01 corporate/institute research
- B02 strategy and business development
- B03 incubation, founder centers, and technology transfer
- B04 startups, ventures, and commercialization
- B05 forecasting and planning
- B06 data analytics and business intelligence
- B07 market intelligence and market research
- B08 consulting and analytics consulting
- B09 digital/web analytics
- B10 digital transformation and digital strategy

The relevance gate is recall-first: a strong B01-B10 family term passes by itself. A weak family term also passes when the advertisement contains an analytical task or is clearly written for an English-language environment. This deliberately sends more borderline jobs to human review so potentially suitable positions are not silently lost. Only the highest family contribution enters the final score, preventing overlapping families from inflating rank. Tie-breakers in `keywords_jobseeking_student_v1.json` choose the primary family; a close runner-up is exported as the secondary family.

The final routes are `PRIORITY` (12+), `REVIEW`, `DE REQUIRED`, `ACADEMISCHE`, and `DROP`. Uncertain relevance and low scores go to `REVIEW`, not `DROP`. `DE REQUIRED` contains B01-B10 matches with a strong German requirement. `ACADEMISCHE` contains explicit doctoral roles plus otherwise uncertain academic-research titles. B2/good German remains a penalty. Immatrikulation, Pflichtpraktikum, remaining study time, work authorization/citizenship/security checks, duration, and weekly-hours conditions remain flags only—never legal conclusions or automatic rejections.

## Setup and usage

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Run one search URL:

```powershell
.\.venv\Scripts\jobfilter run --url "https://www.arbeitsagentur.de/jobsuche/suche?..." --max-ads 20
```

Run every non-comment line in `urls.txt`:

```powershell
.\.venv\Scripts\jobfilter run --urls-file urls.txt
```

Score saved text without network access:

```powershell
.\.venv\Scripts\jobfilter score-text --title "Werkstudent Data Analytics" --file .\ad.txt
```

Fetch and explain one BA detail page:

```powershell
.\.venv\Scripts\jobfilter explain --url "https://www.arbeitsagentur.de/jobsuche/jobdetail/REFERENCE"
```

Each run writes two dated Excel workbooks below `out/run`, both with `PRIORITY`, `REVIEW`, `DE Required`, `Academische`, and `DROPPED` sheets:

- `out/run/jobs_vollzeit_YYYY-MM-DD.xlsx` for ordinary full-time roles
- `out/run/jobs_other_YYYY-MM-DD.xlsx` for Werkstudent, Praktikum, graduate/trainee, part-time, and other accepted early-career formats

After each run, those dated workbooks are merged into `out/jobs_vollzeit.xlsx` and `out/jobs_other.xlsx`. The master workbooks remove duplicate advertisements by canonical URL, keep the latest available record, and sort every sheet by posting date from newest to oldest. Every sheet includes the German federal state and straight-line distance from Heilbronn in kilometres. Listings with multiple resolved cities use the nearest distance; generic locations remain blank.

`seen.sqlite` is created on the first run and prevents the same advertisements from being processed again unless the active history is intentionally reset. `max_ads: 0` and `max_pages: 0` mean unlimited; the default run therefore scrapes to the end of every configured search. A positive command-line limit can still be used for an intentionally bounded run. Same-day output files receive `_2`, `_3`, and later suffixes so earlier batches are not overwritten.

## Tuning `keywords_jobseeking_student_v1.json`

Terms, weights, score bands, gates, penalties, bonuses, flags, regexes, and tie-breakers are loaded dynamically. Edit the JSON rather than Python. Keep all ten families and validate the file after changes:

```powershell
python -m json.tool keywords_jobseeking_student_v1.json > $null
pytest
```

## Calibration after the first real BA run

Inspect both kept and dropped audit rows. Excessive REVIEW volume usually means broad weak terms or analysis tasks should be tightened. Very low recall means employment/relevance evidence or family terms may need loosening. Do not silently change thresholds after one run: collect false positives and false negatives, adjust deliberately, and rerun the fixture suite.

The fetcher respects `robots.txt`, uses polite delays and bounded retries, caches details, prefers the public BA API, and never attempts CAPTCHA/login bypass, proxy rotation, or access-control evasion. Two independent pacing layers are used: per-host delays protect actual network requests, while independently randomized 3–5 second rests apply between advertisements. Longer 60–120 second pauses occur after a newly randomized batch of 50–100 positions, even when pages are served from cache.
