# CLAUDE.md — Vintage Star Wars Sourcing Engine

Working conventions for anyone (human or agent) writing code in this repo.
Product/architecture detail lives in `vintage-sw-sourcing-engine-spec.md`; **this file governs how we build.**

---

## What this project is

A scheduled Python job that scans marketplaces (eBay, Facebook Marketplace via browser-assist, Tier-2 scrapers) for vintage Kenner Star Wars figures, **grades** them with a vision model, screens on price, and emits **buy / negotiate / skip** alerts to Discord. Goal: keep a weekly Whatnot show stocked at low cost.

Buying profile: mid-to-high-grade figures at ~$5/good figure. Weapons and accessories are wanted (price TBD). **Everything must be authentic vintage Kenner — no reproductions, no restored/replacement parts. Authenticity is a hard gate that overrides price.**

---

## Golden rules (read before writing code)

1. **TDD, always.** Write a failing test first, make it pass with the minimal change, then refactor. Every bug fix begins with a regression test that reproduces it.
2. **Separation of concerns.** `adapters/` *fetch*, `core/` *decides*, `storage/` *persists*, `alerts/` *deliver*. Layers talk through typed interfaces, not internals.
3. **The core is source-agnostic.** Nothing in `core/` may import an adapter or branch on a hardcoded source name. Adapters normalize to the `Listing` schema; the core only sees `Listing` objects.
4. **Config over hardcoding.** Thresholds, cadences, enabled sources → the `config` table, never literals in code.
5. **Idempotency.** A listing is processed and alerted at most once (dedupe on `(source, listing_id)`).
6. **Graceful degradation.** One source failing logs and is skipped; it never aborts the run.
7. **Cost invariants (never regress these):**
   - Never call the vision model before the cheap pre-filter passes.
   - Always check `vision_cache` before a vision call, keyed by a hash of the listing's **full image set** (all photos hashed together — grading is one request per listing, not per photo).
   - Never re-process a deduped listing.
8. **ToS guardrails.** No headless Facebook scraping — FB is human-in-the-loop only. Never auto-message sellers on any platform. Scrape only public listing data on Tier-2 sources.
9. **Authenticity is a hard gate — it overrides price.** Photo analysis *flags repro risk*, it never *certifies authenticity*. No item above `max_repro_risk_for_autobuy` (default `low`) is ever auto-bought — it routes to **manual review**. A cheap repro-risky lot is a skip, never a buy. Bias buys toward `returns_accepted: true` so a wrong call is reversible. Final authenticity is a human in-hand step outside the code; the system must never present itself as the guarantee.
10. **Pure business logic.** `decision.py`, `negotiation.py`, `prefilter.py`, `authenticity.py` are pure functions (inputs → outputs, no I/O). Keep them that way.

---

## Repo layout

```
sw_sourcing/
  adapters/            # one module per source; implement the Adapter protocol
    base.py            #   Adapter.fetch() -> list[Listing]
    ebay.py
    tier2_apify.py
    facebook_assist.py
  core/                # source-agnostic; pure where possible
    schema.py          #   Listing model (pydantic)
    prefilter.py       #   cheap keyword/price screen (pure)
    dedupe.py          #   seen-listing check
    vision.py          #   Claude grade + repro-risk gate; client injected
    decision.py        #   buy / negotiate / review / skip (pure)
    negotiation.py     #   offer math (pure)
    authenticity.py    #   repro-text screen + risk-routing rules (pure)
  storage/
    db.py              # SQLite access
    config.py          # typed config accessor
  alerts/
    discord.py         # optional live push
    email.py           # primary channel: periodic digest, decoupled from scan cadence
  diagnostics.py       # automatic bug reporting (see below) -- not auto-fix
  lock.py              # non-blocking file lock, prevents overlapping scan runs
  pipeline.py          # orchestrates one run (wires the pieces together)
  cli.py               # cron entrypoint: scan / send-report / report-bug / config
tests/                 # mirrors the package tree
  unit/                # pure-logic tests, no network
  fixtures/            # recorded API/scraper/vision responses
  integration/         # opt-in, real creds/live claude CLI, marked @integration
bug_reports/           # gitignored; written by diagnostics.py, reviewed by hand
```

## Module responsibilities

| Module | Owns | Must NOT |
|---|---|---|
| `adapters/*` | Fetching + normalizing to `Listing` | Contain business logic or price rules |
| `core/decision.py` | Buy/negotiate/review/skip classification | Do I/O, know about a source |
| `core/negotiation.py` | Offer + cutoff math | Do I/O |
| `core/authenticity.py` | Repro-text screen + risk routing | Certify authenticity (only flags risk) |
| `core/vision.py` | Prompt + parse grade & repro-risk JSON; deterministically recomputes `target_grade_count`/`authentic_weapon_count` from the parsed `items` list | Hardcode a client; caching lives here + storage; **trust the model's own aggregate counts** |
| `storage/*` | SQLite reads/writes | Contain decision logic |
| `alerts/*` | Formatting + sending | Decide what qualifies as an alert |
| `diagnostics.py` | Writing bug reports (context + traceback + repro) for human review; cooldown-gates repeat reports for the same failure key so a persistently broken source/listing doesn't spam a fresh report every run | **Auto-fix or self-modify code.** This project deliberately has no autonomous self-healing -- errors are captured for periodic manual review with Claude Code, never acted on unattended |
| `lock.py` | Non-blocking file lock so overlapping `scan` runs skip cleanly instead of racing on the SQLite file | Block/wait for the lock -- a stuck lock must never hang cron |
| `pipeline.py` | Wiring + orchestration | Reimplement any of the above |

---

## Dev workflow (the TDD loop)

1. Write or adjust a test in `tests/` describing the desired behavior. Run `pytest` → **red**.
2. Implement the smallest change that passes. Run `pytest` → **green**.
3. Refactor for clarity; tests stay green.
4. `black .` → `ruff check .` → `mypy sw_sourcing` before every commit.
5. Commit with a message that names the behavior, not the file.

---

## Testing standards

- **Framework:** `pytest`.
- **Unit tests** cover the pure logic (`decision`, `negotiation`, `prefilter`, cost/grade math) with **no network** — these should be fast and exhaustive on edge cases (zero good figures, all-damaged lot, exactly-at-threshold, negotiate-band boundaries).
- **Adapters** are tested against **recorded fixtures** in `tests/fixtures/` (saved API/scraper JSON), never live endpoints.
- **Vision** code is tested with **canned model responses** — assert correct parsing, `target_grade_count` computation, cache hit/miss, and low-confidence → `needs review` routing. Mock the Claude client.
- **Integration tests** (real creds, live calls) live in `tests/integration/`, marked `@pytest.mark.integration`, and are **excluded from the default run and CI**. Run manually.
- **Regression-first:** reproduce every bug with a failing test before fixing.
- Aim for high, meaningful coverage on `core/` (the money logic); don't chase coverage on thin I/O wrappers.

---

## Code style

- **Formatter: Black.** CI-enforced, non-negotiable. Default line length (88).
- **Linter: ruff** (includes import sorting).
- **Types: mypy.** Public functions are fully typed; the `Listing` schema and vision output are `pydantic` models.
- Small functions, single responsibility, docstrings on public functions and modules.
- **No secrets in code.** API keys via env vars / `.env` (gitignored). Never log secrets or full PII from listings.

---

## Key commands

```bash
# setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# quality gate (run before commit)
black .
ruff check .
mypy sw_sourcing
pytest                      # unit + fixture tests
pytest -m integration       # opt-in, needs real creds

# run
python -m sw_sourcing.cli scan                     # full scheduled run
python -m sw_sourcing.cli scan --source ebay        # single source
python -m sw_sourcing.cli send-report               # email digest of unreported alerts
python -m sw_sourcing.cli report-bug "note"         # manually log something odd
python -m sw_sourcing.cli config list               # print every config key + value
python -m sw_sourcing.cli config get target_per_figure
python -m sw_sourcing.cli config set target_per_figure 5.5
```

`scan` and `send-report` run on independent cron schedules -- changing either's
cadence is a one-line crontab edit, not a code change. `scan` takes a
non-blocking lock (`lock.py`) so an overlapping run skips cleanly rather than
racing the previous one on the SQLite file.

---

## Business rules that must not silently change

Changing any of these requires an updated test **and** a config default change — never a bare edit:

- `target_per_figure` (default 5.00), computed against **mid+ grade, undamaged, `repro_risk: low`** figures only.
- `grade_floor` = "mid" — the vision gate grades; low-grade and damaged figures do not count.
- **Weapons and accessories are wanted** (target price TBD, configurable) — but subject to the same authenticity gate as everything else.
- **Authenticity is mandatory and overrides price.** `max_repro_risk_for_autobuy` = "low"; anything above routes to manual review. Disclosed-repro listings (keyword blocklist) are skipped. This rule may not be relaxed for a cheaper price.
- `negotiate_band_pct` (default 0.35); negotiate alerts only on offer-accepting, haggle-friendly sources — never live auctions.

---

## Definition of done (per change)

- Failing test written first, now passing.
- `black` / `ruff` / `mypy` clean.
- Cost invariants and separation-of-concerns respected.
- Config-driven values not hardcoded.
- Heartbeat/logging updated if a new source or failure mode was added.

## Claude API Access

No `ANTHROPIC_API_KEY` is available. Use the `claude` CLI for all LLM calls:

```bash
# Text prompt
claude -p "Your prompt here"

# With piped input
echo "analyze this" | claude -p

# With an image file
claude -p "Analyze this image and return JSON" --image /path/to/image.jpg

# Capture output in a script
result=$(claude -p "Summarize: $(cat metadata.json)")

For this cron job: pass image paths via --image and inline metadata in the prompt string.
Output is plain text on stdout; structure the prompt to request JSON if you need machine-readable results.

A few notes on the image flag: run `claude --help` to confirm the exact flag name (`--image` may vary by version). If it doesn't support a direct image flag, the workaround is base64-encoding the image and including it in the prompt, but confirm the flag first.
