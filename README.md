# Brand Watch — AI Brand Sentiment Monitor

A single-page web app that turns one company website URL into a full brand
reputation report: a 0–100 Reputation Score, sentiment charts, complaint and
praise themes, urgent flags, quick wins and a live mentions feed — powered by
the user's **own free** Google Gemini key and Reddit API credentials.
Distributed free as a B2B lead magnet: 2 free runs, then a Name / Business
Email / Company gate that saves the lead to SQLite and unlocks unlimited runs,
the PDF report and the source-code blueprint download.

**£0 infrastructure:** no paid APIs, no key storage. User keys live in browser
localStorage and travel per-request; they are never written to disk, logged,
or put in the database.

## Architecture at a glance

```
app.py                      FastAPI backend — the whole pipeline
templates/index.html        The single-page dashboard
static/app.js, style.css    Dashboard logic + styling (print CSS = PDF report)
skills/                     4 SKILL.md files — Gemini system prompts, loaded
                            from disk at request time, used verbatim
blueprint/README.md         README shipped inside the blueprint zip download
leads.db                    SQLite, created automatically (gitignored)
```

Pipeline: **discover** (`/api/discover`, brand-discovery skill) → user approves
watchlist → **run** (`/api/run`): concurrent ingestion (Reddit OAuth +
DuckDuckGo/Bing search-engine bypass for X + direct URL fetches) → parsing
(universal-parser + sentiment-calibration skills, 30k-char chunks, defensive
JSON handling with one retry) → scoring (reputation-scoring skill).

## Run locally

```bash
pip install -r requirements.txt
python app.py                # or: uvicorn app:app --reload
```

Open http://localhost:8000. You need nothing else — keys are pasted into the
dashboard Settings panel by each user:

- Gemini key(s): https://aistudio.google.com/apikey (free). **Tip:** add two or
  more keys in Settings — the backend spreads calls across them and fails over
  when one hits its free-tier rate/quota limit, so a single key running out no
  longer stalls a run. The `GEMINI_API_KEY` env var also accepts a
  comma-separated list of server-side default keys.
- Reddit: https://www.reddit.com/prefs/apps → "create another app" → type
  **script** → use the Client ID + Secret + your username (optional; if left
  blank, Reddit coverage falls back to the search-engine queries)

### Environment variables (all optional except the export one)

| Variable | Purpose |
|---|---|
| `EXPORT_PASSWORD` | **Required to download leads.** Protects `/export` (HTTP basic auth, username `admin`) |
| `GEMINI_API_KEY` | Server-side default Gemini key (normally leave unset) |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` / `REDDIT_USERNAME` | Server-side Reddit defaults |
| `GEMINI_MODEL` | Defaults to `gemini-2.0-flash` |
| `PORT` | Defaults to `8000` |

## Deploy on a free tier (Render / Railway)

**Render (one click):** this repo ships a `render.yaml` blueprint, so go to
https://dashboard.render.com/blueprints → **New Blueprint Instance** → connect
this GitHub repo and pick the branch → Render reads `render.yaml` and builds
everything itself. It will prompt you once for `EXPORT_PASSWORD` (the password
that protects your leads CSV at `/export`) — choose anything. The free
instance sleeps when idle, which is fine for a lead magnet; it wakes on the
first visit.

**Railway:** New Project → Deploy from GitHub repo → it autodetects Python and
uses the `Procfile`; add `EXPORT_PASSWORD` under Variables → Settings →
Networking → Generate Domain.

After deploying, open the public URL, click **Settings** in the top bar, paste
a Gemini key, and run the 10-step test script below against the live site.

Note: SQLite lives on the instance disk. On Render's free tier the disk is
ephemeral, so download your leads CSV regularly (or attach a persistent disk /
move to a managed DB when volume justifies it).

## Where to change branding

- **App name, headline and tagline:** `templates/index.html` — the `<title>`
  tag, the `.logo` in the top bar, and the hero section copy.
- **PDF report footer:** `templates/index.html` — search for
  `[[ YOUR COMPANY NAME` inside the `report-footer` div and replace both
  placeholders with your company name and URL.
- **Colours:** the `:root` variables at the top of `static/style.css`
  (`--accent` is the primary brand colour).
- **Free-run allowance:** `FREE_RUNS` in `app.py`.

## Exporting your leads

Set `EXPORT_PASSWORD`, then visit `https://your-app/export` — log in with
username `admin` and that password to download `brand-watch-leads.csv`
(id, name, business_email, company_name, monitored_brand, created_at).

## Automated funnel check (no API quota needed)

```bash
python scripts/mock_e2e.py
```

Spins up a mocked Gemini API plus fake review pages and drives the whole
funnel — discovery, two runs, the credit gate, lead capture, unlocked runs,
the blueprint zip and the CSV export — printing PASS/FAIL per check. It also
deliberately feeds the app fenced and malformed JSON to prove the defensive
parsing works. Run it after any code change before deploying.

## 10-step manual test script (full funnel)

Use a fresh browser profile (or clear cookies for the site) so credits start
at 2. Have a Gemini key ready; Reddit credentials optional.

1. **Discovery** — click **Analyse my brand →** with no key set: the Settings
   modal opens with a "required" error on the Gemini field. Paste your key,
   save, enter a real company website URL in the hero bar and click
   **Analyse my brand →** again. Expect a company profile panel (brand,
   industry, aliases…) and a watchlist of max 8 source cards with priority
   badges. The top-bar badge reads "Free analysis runs remaining: 2 of 2".
2. **Watchlist editing** — untick one recommended card; click **+ Add URL** and
   add your Trustpilot or G2 profile URL. The custom card appears ticked.
3. **Run 1** — click **Run Monitoring ▶**. Expect the progress panel, then
   results: score gauge with band label, sentiment doughnut, platform bar
   chart, theme cards, urgent flags / quick wins, the mentions feed with
   sentiment badges and "View original" links, and per-source fetch status
   rows (any failed source shows red with a reason — the run still completes).
   Badge now reads "1 of 2".
4. **Run 2** — run again. Completes normally; badge reads "0 of 2".
5. **Credit limit** — click **Run Monitoring ▶** a third time. The blocking
   unlock modal appears instead of a run.
6. **Gate validation** — submit empty → error. Enter `test@gmail.com` → soft
   warning about personal addresses (not blocked); submit again → accepted.
   Use name "Test User", company "Test Co".
7. **Lead saved** — modal closes, the run starts automatically, badge switches
   to "✓ Unlimited access unlocked". Verify in another terminal:
   `sqlite3 leads.db "SELECT * FROM subscribers;"` shows the row.
8. **Blueprint download** — click **Download Source Code Blueprint**; a
   `brand-watch-blueprint.zip` downloads. Unzip it and confirm: README.md,
   app.py, skills/, templates/, static/, `.env.example` — and grep it to
   confirm **no API keys and no leads.db** inside.
9. **PDF report** — click **Download PDF Report**; the browser print dialogue
   opens. The preview hides the app chrome, buttons and inputs and shows the report
   header, score, charts, themes, executive summary, a mentions sample and
   the branded footer placeholders. Save as PDF.
10. **CSV export** — restart the app with `EXPORT_PASSWORD=secret python app.py`,
    visit `/export`, log in as `admin` / `secret`, and confirm the CSV contains
    the lead from step 7. (Without the env var, `/export` returns a clear
    "export disabled" message; with wrong credentials, 401.)
