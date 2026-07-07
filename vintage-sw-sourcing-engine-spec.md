# Vintage Star Wars Sourcing Engine — Project Spec

*A scheduled job that scans marketplaces for authentic vintage Kenner Star Wars figures and weapons/accessories, grades them, screens for authenticity and price, and alerts you to deals worth buying or lots worth negotiating — to keep a weekly Whatnot show stocked at low cost.*

---

## 1. Goal

Surface, on a schedule, the listings worth your attention across multiple marketplaces, pre-screened so that:

- **Junk and fakes are filtered out** (broken/low-grade figures; any reproduction items).
- **Authentic mid-to-high-grade lots at/under target** are flagged to **buy now**.
- **Overpriced-but-close lots** are flagged to **negotiate** down to target, with a suggested offer.
- **Anything with authenticity doubt** routes to **manual review**, never auto-buy.

The output is a short, high-signal alert feed you triage from your phone.

**Buying profile:** mid-to-high grade figures at **~$5 per good figure**. **Weapons and accessories are wanted** (target price configurable, TBD). **Authenticity is mandatory — 100% authentic vintage Kenner, no reproductions, no restored/replacement parts.** Authenticity is a hard gate that **overrides price** (see §8).

---

## 2. Deployment decision — script on a schedule, not an app

**Build it as a server-side script run by a scheduler (cron). Do not build an iOS app.**

- iOS background processing is *opportunistic*, not periodic — you cannot get reliable "scan every 30 minutes." Wrong tool.
- The phone's only job is **receiving alerts and reviewing** — Discord push (and optionally Airtable) provide that for free.

**Where the job runs (cheapest first):**

| Option | Cost | Notes |
|---|---|---|
| Raspberry Pi / spare always-on computer + cron | ~free (electricity) | Best start if you own the hardware |
| Serverless cron (Cloudflare Workers cron / GitHub Actions schedule) | free tier | Off your home network; confirm current limits |
| Small cloud VM | a few $/mo | Always-on without owning hardware |

Where cron runs is nearly free either way. **Real spend = vision calls + scraper credits** (see §12).

---

## 3. Pipeline (architecture)

```
Sources → Adapters (normalize) → Cheap pre-filter + repro-text screen → Dedupe
       → Vision grade + repro-risk gate → Count target-grade figures
       → Decision engine → { Buy now | Negotiate | Manual review | Skip } → Alerts
                                     ↑                                          ↓
                                  Config                               Heartbeat summary
```

Design rule: **every source is a plug-in adapter that outputs one common shape.** Everything downstream is source-agnostic.

---

## 4. Tech stack (low-cost, concrete)

| Layer | Choice | Why |
|---|---|---|
| Language / runtime | Python | Best library support for eBay, scraping, the Anthropic SDK |
| Orchestration | `cron` calling one script | Cheapest; no hosted orchestrator |
| eBay source | eBay Browse API (direct) | Sanctioned, free, reliable |
| Tier-2 sources | Apify actors (or similar managed scraper) | Rent the anti-bot maintenance |
| Facebook Marketplace | Browser-assist / human-in-loop (v1) | Avoid ban risk (see §7) |
| Vision | `claude` CLI (no API key provisioned for this project) | Grade + repro-risk pass |
| Data store | SQLite file | Zero-cost, local; dedupe + config + history |
| Alerts | Email digest (SMTP), Discord webhook optional | Digest decouples notification cadence from scan cadence; Discord push available if configured |
| (Optional) Review UI | Airtable | Nicer triage queue; add later |

> Verify current Claude model names and per-token pricing at https://docs.claude.com/en/api/overview before finalizing the budget.

---

## 5. Common listing schema

```json
{
  "source": "ebay",
  "listing_id": "1234567890",
  "url": "https://...",
  "title": "Vintage Kenner Star Wars loose figure lot of 12 w/ weapons",
  "description": "...full text for the repro screen...",
  "price": 60.00,
  "shipping": 8.50,
  "buying_option": "best_offer",       // auction | fixed_price | best_offer
  "offers_accepted": true,
  "returns_accepted": true,            // key for authenticity risk (reversible)
  "seller_feedback": 0.995,
  "location": "OH, USA",
  "images": ["https://...", "https://..."],
  "fetched_at": "2026-07-06T14:00:00Z"
}
```

---

## 6. Source adapters + cadence

| Tier | Sources | Access | Cadence |
|---|---|---|---|
| 1 | eBay | Browse API (sanctioned) | Every 15–30 min |
| 2 | Mercari, OfferUp, shopGoodwill, estate-auction aggregators (HiBid, LiveAuctioneers, EstateSales.net, AuctionNinja) | Managed scraper | Every 1–3 hr |
| 3 | **Facebook Marketplace (v1)**, Craigslist | Browser-assist (human logged in) | On-demand while browsing |
| 4 | Collector forums/groups, Instagram, in-person estate/garage sales | Manual | N/A |

**Facebook** is human-in-the-loop in v1: a browser extension forwards listings you're already viewing into the same pipeline. No headless scraping, no auto-messaging sellers.

Each adapter runs **isolated** — a failure logs and is skipped without stopping the run.

---

## 7. Vision grade + repro-risk gate

One structured call per listing that clears the pre-filter. Send **all photos in a single request**. The gate does two jobs: **grade** each item, and **flag reproduction risk**.

**Grade scale:** `high` (sharp, minimal wear) · `mid` (clean, minor wear — buy floor) · `low` (heavy wear/fading) · `damaged` (missing/broken — reject) · `uncertain` (photo insufficient → human review).

**Repro risk:** `low` / `elevated` / `high` per item, **biased to caution** — any uncertainty about a weapon or accessory is at least `elevated`. This is a **risk signal, not a verdict** (see §8).

**Output (strict JSON):**

```json
{
  "figure_count": 12,
  "items": [
    {"id": 1, "type": "figure",    "grade": "high", "issues": [],                   "repro_risk": "low",      "confidence": 0.9},
    {"id": 2, "type": "figure",    "grade": "damaged","issues": ["missing arm"],    "repro_risk": "low",      "confidence": 0.8},
    {"id": 3, "type": "weapon",    "grade": "mid",  "issues": [],                   "repro_risk": "elevated", "confidence": 0.5,
     "repro_notes": "plastic looks glossy/new; expected wear absent"},
    {"id": 4, "type": "accessory", "grade": "uncertain","issues": [],               "repro_risk": "high",     "confidence": 0.3}
  ],
  "target_grade_count": 1,             // figures: mid+ grade, undamaged, repro_risk low
  "authentic_weapon_count": 0,         // weapons/accessories with repro_risk low
  "photo_quality": "clear",
  "notes": "one weapon flagged for repro review"
}
```

**Rules:**

- **`target_grade_count` counts only figures that are mid+ grade, undamaged, AND `repro_risk: low`.**
- **Conservative counting** — ambiguous piles return a range + low confidence.
- **`uncertain` grade or non-low repro risk → routed, never silently passed.**
- **`target_grade_count` and `authentic_weapon_count` in the model's JSON are advisory only.** The pipeline deterministically recomputes both in code from the itemized `items` list before either value touches cost math or the decision engine — a model arithmetic slip must never silently drive a buy.

**Cost control:** only call vision after the pre-filter passes; cache by a hash of the listing's **full image set** (all photos hashed together, since grading is one request per listing, not per photo); cheap model first, escalate low-confidence cases.

---

## 8. Authenticity — the trust gate (mandatory, overrides price)

**Only authentic vintage Kenner items go on the show. No reproductions, no restored/replacement parts sold as original.** This is a hard gate. A cheap lot with authenticity doubt is **not** a deal.

**Honest limitation:** authenticity **cannot be guaranteed from photos.** High-quality reproduction weapons/accessories fool image analysis and experienced collectors. The automated layers **reduce risk and flag concerns; they never certify.** Certification is in-hand, by you, before listing.

**Four-layer defense:**

1. **Text screen (cheapest, first).** Auto-reject/route listings whose text discloses `repro`, `reproduction`, `replacement`, `restored`, `custom`, `aftermarket`, `not original`, etc. Configurable blocklist.
2. **Vision repro-risk flag** (see §7) — per-item risk score, biased to caution.
3. **Seller + return signals.** High feedback, specialization, and especially `returns_accepted: true` make a wrong call reversible.
4. **Mandatory in-hand authentication.** Every item is authenticated by you against references **before it goes on the show.** This is the guarantee.

**Decision impact:** repro risk above `low` → **manual review, never auto-buy.** The buy-now path requires all counted items at `repro_risk: low`; strongly prefer `returns_accepted: true` on anything containing weapons/accessories.

---

## 9. Decision engine — four outcomes

Compute **cost per target-grade figure** = `(price + shipping) / target_grade_count`; authentic weapons/accessories add value. Then sort:

| Outcome | Condition |
|---|---|
| **Buy now** | cost/figure ≤ `target_per_figure`, all counted items `repro_risk: low`, (prefer returnable) |
| **Negotiate** | over target but within band, offers accepted, low repro risk |
| **Manual review** | any `elevated`/`high` repro risk, or `uncertain` grade — authenticity decision needs eyes |
| **Skip** | too far over target, too few good figures, disclosed repro, or fails the gate |

**Negotiate band** — config value (~30–40% over target). Offer slightly below target; never surface a lot that loses money after fees at its best realistic price. Negotiate only on offer-accepting, haggle-friendly sources — never live auctions.

---

## 10. Config (edit without touching code)

| Key | Value | Meaning |
|---|---|---|
| `target_per_figure` | 5.00 | Buy-now threshold, $/target-grade figure |
| `target_per_weapon` | TBD | Weapon/accessory threshold (set once you have comps) |
| `grade_floor` | "mid" | Minimum grade counted |
| `authenticity_required` | true | Hard gate — non-negotiable |
| `repro_keyword_blocklist` | ["repro","reproduction","replacement","restored","custom","aftermarket","not original"] | Text screen |
| `max_repro_risk_for_autobuy` | "low" | Above this → manual review, never auto-buy |
| `prefer_returnable` | true | Bias buys toward reversible purchases |
| `negotiate_band_pct` | 0.35 | How far over target still counts as negotiable |
| `max_damage_ratio` | 0.20 | Skip lot if > this share damaged/low |
| `confidence_floor` | 0.5 | Below this → human review |
| `sources_enabled` | ["ebay","facebook","mercari"] | Which adapters run (FB in v1) |
| `prefilter_required_keywords` | ["kenner","vintage","star wars"] | Cheap pre-vision topic screen (title/description, any match) |
| `prefilter_max_listing_price` | 500.00 | Cheap pre-vision price ceiling (price + shipping) |

Stored in a SQLite `config` table (or Airtable) so it's editable from your phone.

---

## 11. Data store (SQLite schema)

```sql
CREATE TABLE seen_listings (
  source TEXT, listing_id TEXT, first_seen TEXT, last_seen TEXT,
  PRIMARY KEY (source, listing_id)
);
CREATE TABLE vision_cache (image_set_hash TEXT PRIMARY KEY, result_json TEXT, created_at TEXT);
CREATE TABLE alerts (
  id INTEGER PRIMARY KEY, source TEXT, listing_id TEXT,
  title TEXT, url TEXT, image_url TEXT,
  outcome TEXT,                      -- buy | negotiate | review
  cost_per_figure REAL, target_grade_count INTEGER,
  max_repro_risk TEXT, returns_accepted INTEGER,
  suggested_offer REAL, alerted_at TEXT,
  reported_at TEXT                   -- NULL until picked up by a digest send
);
CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY, started_at TEXT, sources_ok TEXT,
  sources_failed TEXT, listings_seen INTEGER, alerts_sent INTEGER
);
```

---

## 12. Cost control

eBay API, Discord, SQLite, compute are ~free. Spend = **vision calls + scraper credits**. Levers, in order:

1. **Pre-filter (incl. repro-text screen) before vision** — reject fakes and irrelevant listings for free.
2. **Cache vision by image-set hash** (all photos in a listing hashed together, not per photo).
3. **Dedupe** — process a listing once, ever.
4. **Cheap model first, escalate rarely.**
5. **Scan defended sources less often.**

These are **invariants** — see `CLAUDE.md`.

---

## 13. Reliability

- **Source isolation** — one adapter failing never stops the run; a bad listing never stops the rest of its batch either.
- **Backoff + jitter** — respect rate limits.
- **Heartbeat** — each run posts a one-liner to Discord, if configured (optional).
- **Bug reports, not auto-fix** — adapter/listing failures and unhandled errors write a markdown report (context + traceback + repro) to `bug_reports/` for periodic manual review with Claude Code. Nothing self-modifies unattended.
- **Managed scrapers** for defended sites.

---

## 14. Engineering standards & repo layout

Full working conventions live in the companion **`CLAUDE.md`**. Summary: **TDD**; **separation of concerns** (adapters fetch, core decides, storage persists, alerts deliver; core is source-agnostic); **small typed modules** with dependency injection; **config over hardcoding**; **Black** (CI-enforced) + ruff + mypy; **idempotency & graceful degradation**; **authenticity is a hard gate enforced in code and tests**.

```
sw_sourcing/
  adapters/   base.py ebay.py tier2_apify.py facebook_assist.py
  core/       schema.py prefilter.py dedupe.py vision.py decision.py negotiation.py authenticity.py
  storage/    db.py config.py
  alerts/     discord.py
  pipeline.py cli.py
tests/        unit/ fixtures/ integration/
```

---

## 15. Build phases

1. **MVP:** eBay adapter → pre-filter (+ repro-text screen) → vision grade+repro gate → cost/figure → Discord buy-now/review alerts.
2. **Negotiate band:** three-way decision + suggested offers (eBay Best-Offer first).
3. **Facebook (v1 scope):** browser-assist extension feeding the pipeline.
4. **Widen the net:** remaining Tier-2 adapters, one at a time.
5. **Nice-to-haves:** Airtable review queue, weapon comp pricing, self-built sold-price database, per-category mix tracking.

---

## 16. Feeding the show

- **Slot:** commit to one weekly — Tuesday 2 PM ET (low-competition) or Thursday 5 PM ET (higher ceiling). Target **2 hrs, growing toward 3**.
- **Naming:** lead with keywords — "Vintage Kenner Star Wars — …"; signal loose vs. carded, don't lead with "loose."
- **Restock rhythm:** scanner runs continuously; do a **batch buy/negotiate/authenticate review a few days before each show** so items arrive, are authenticated in hand, and prepped in time.
- **Authenticity is your on-air story** — "everything authenticated, no repros" is a trust differentiator worth stating on stream.

---

## 17. Resolved decisions

- **Facebook:** in scope for v1 (human-in-the-loop).
- **Figures:** `$5` / mid-to-high-grade figure.
- **Weapons/accessories:** wanted (target price TBD, configurable).
- **Authenticity:** mandatory, 100% authentic, no repro/restored — hard gate that overrides price; in-hand authentication required before listing.
- **Grade floor:** `mid`.

Still open: weapon target pricing (needs comps); rule-of-thumb thresholds vs. a self-built sold-price database later; Discord-only review vs. Airtable from day one.

---

## 18. Legal / ToS notes (practical, not legal advice)

- eBay Browse API use is sanctioned.
- Scraping Facebook Marketplace violates Meta's ToS and risks bans — hence human-in-the-loop in v1.
- Tier-2 scraping via managed services is a gray area; public listing data only, no auto-messaging.
- Not legal advice; if you productize, consult counsel.
