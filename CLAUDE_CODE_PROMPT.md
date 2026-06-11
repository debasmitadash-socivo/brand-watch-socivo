# CLAUDE CODE BUILD PROMPT — AI Brand Sentiment Monitor ("Brand Watch")

Copy everything below this line into Claude Code, with the `/skills` folder placed in the project root before you start.

---

Build a complete, runnable, single-page web application: an **AI-powered Brand Sentiment Monitor** distributed free as a B2B lead magnet. Optimised for B2B companies in the UK and USA. Follow this spec exactly. Where this prompt and a skill file conflict, the skill file wins.

## 0. Ground Rules

- Stack: Python backend (FastAPI preferred, Flask acceptable), one HTML page with vanilla JS or a single self-contained React file, SQLite, Chart.js for charts. No build pipeline needed to run locally. `pip install -r requirements.txt` then one command to start.
- **£0 infrastructure model:** the app runs entirely on user-supplied keys. The user pastes their own free Google Gemini API key, and their own Reddit Client ID / Client Secret / Username, into the dashboard. Keys live in browser localStorage only and are sent per-request to the backend; they are NEVER written to disk, logged, or stored in the database.
- UK spelling in all UI copy and prompts.
- The four files in `/skills` are the intelligence layer. Load them from disk at request time (do not paste their contents into the code) and use them verbatim as Gemini system prompts as described below. Do not rewrite them.

## 1. The Pipeline (this is the core product flow)

**Step 1 — Discovery (the magic moment).**
The user enters only their company website URL (plus optional industry override). The backend fetches the homepage HTML (and /about if reachable), strips it to text, and calls Gemini with `skills/brand-discovery/SKILL.md` as the system prompt. Gemini returns a JSON company profile + recommended watchlist (max 8 sources, B2B platforms like G2/Capterra/Clutch/Trustpilot weighted high) + search queries.

**Step 2 — Watchlist approval.**
Render the watchlist as tickable cards (platform, why it matters, priority badge). The user can untick any, and add custom URLs via an "Add URL" button (dynamic list, no limit). Nothing is scraped until the user clicks "Run Monitoring".

**Step 3 — Ingestion.** Three cost-free methods:
- **Reddit (user OAuth):** connect to Reddit's API with the user's own credentials, search the brand name and every alias from the profile. Private free limit ~100 requests/minute.
- **X (search-engine bypass):** X blocks free access, so scrape public search-engine results (DuckDuckGo HTML endpoint first, fallback to another engine) for the `site:x.com "Brand"` queries generated in Step 1. Collect the indexed snippets and links.
- **Custom/recommended review URLs:** fetch each approved URL, strip scripts/styles/nav, keep the visible text container.
Run fetches concurrently with sensible timeouts; one failed source must never kill the run — report per-source status in the UI.

**Step 4 — Parsing.**
For each raw text dump, call Gemini with a system prompt built by concatenating `skills/universal-parser/SKILL.md` + `skills/sentiment-calibration/SKILL.md`, with the placeholders [BRAND_NAME], [ALIASES], [USER_INDUSTRY] substituted from the Step 1 profile. Chunk dumps over ~30k characters. Strip any markdown fences defensively and validate JSON; on parse failure, retry once with a correction instruction, then skip the chunk and log it.

**Step 5 — Scoring.**
Concatenate all parsed mentions and the company profile, call Gemini with `skills/reputation-scoring/SKILL.md` as the system prompt. The returned JSON powers the dashboard.

## 2. The Dashboard (single page, professional, modern)

- **Configuration sidebar:** Gemini key, Reddit credentials (password-masked), website URL, optional industry, the dynamic custom-URL list.
- **Watchlist panel** (after Step 1): the tickable source cards.
- **Results panel** (after a run):
  - Reputation Score gauge (0–100) with band label (At Risk / Mixed / Healthy / Strong / Exceptional) and a `low_data` warning badge when set
  - Doughnut chart: positive/neutral/negative split
  - Bar chart: mentions by platform
  - Top 3 complaint themes and top 3 praise themes as cards with counts and example quotes
  - Urgent flags and quick wins lists
  - **Live Mentions Feed:** scrolling table — platform tag, text snippet, coloured sentiment badge, relevance/facet tag, "View original" link button
  - Per-source fetch status indicators
- Everything renders dynamically from the JSON payloads.

## 3. Lead Gating (the entire commercial point — do not skip)

- Track usage with a browser cookie `usage_credits`. The user gets exactly **2 free analysis runs**.
- On the **3rd run attempt**, OR when clicking **"Download PDF Report"** or **"Download Source Code Blueprint"**, show a blocking modal requiring **Name, Business Email, Company Name**. Validate the email format and reject obvious free-mail throwaways softly (warn, don't hard-block).
- On submit: save the lead to SQLite and unlock unlimited runs + both downloads for that browser.

## 4. Storage

- SQLite, zero config. One table `subscribers`: id, name, business_email, company_name, monitored_brand, created_at.
- Add a `/export` route protected by HTTP basic auth (password from a single environment variable) that downloads the table as CSV.

## 5. Exports

- **PDF Report:** no backend PDF engine. Use the browser's native print flow with print-media CSS that hides all inputs, key fields, buttons and the sidebar — leaving a clean report: score, charts, themes, executive summary, mentions sample, and a branded footer (company name + URL placeholders clearly marked for me to fill in).
- **Source Code Blueprint (.zip):** backend compiles a zip of a sanitised local template of the app. **Absolutely zero hardcoded keys or database records inside.** It reads keys strictly from environment variables. Include a Claude-ready README.md written so a non-technical user can upload the folder to an AI assistant and ask it to install dependencies, set up a virtual environment, and run it locally.

## 6. Error Handling & Honesty Rules

- Every Gemini call wrapped in try/except with the fence-stripping JSON parse described above.
- If a source returns nothing, show "0 mentions found" for it — never fabricate sample data anywhere in the app.
- If Gemini key is invalid or quota-exhausted, show a friendly message with a link to get a free key.
- Rate-limit politely: small randomised delays between search-engine requests, realistic User-Agent header.

## 7. Deliverables Checklist

1. Working app: `app.py` (or equivalent), `templates/index.html` or single React file, `static/` assets
2. `/skills` loaded from disk at runtime (the four provided files, untouched)
3. `requirements.txt`
4. The blueprint-zip generation route
5. `README.md` for me (the creator): how to run locally, how to deploy on a free tier (Render/Railway), where to change branding, how to export leads
6. A 10-step manual test script I can follow to verify the full funnel: discovery → watchlist → run → results → 2-credit limit → gate → lead saved → downloads unlocked → PDF → export CSV

Build it completely. Do not stub out the ingestion, gating, or export logic.
