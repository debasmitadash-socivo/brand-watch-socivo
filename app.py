"""
Brand Watch — AI-powered Brand Sentiment Monitor.

£0-infrastructure model: the app runs entirely on user-supplied keys
(Google Gemini + Reddit OAuth credentials), pasted into the dashboard and
sent per-request. Keys are NEVER written to disk, logged, or stored in the
database. The only thing persisted is the leads table (SQLite).

Run locally:
    pip install -r requirements.txt
    python app.py            # or: uvicorn app:app --reload
"""

import asyncio
import csv
import io
import json
import os
import random
import re
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from secrets import compare_digest
from urllib.parse import parse_qs, urljoin, urlparse, unquote

import httpx
from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               Response, StreamingResponse)
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------- constants

BASE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = BASE_DIR / "skills"
DB_PATH = BASE_DIR / "leads.db"

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_API_BASE = os.environ.get("GEMINI_API_BASE",
                                 "https://generativelanguage.googleapis.com")
GEMINI_URL = f"{GEMINI_API_BASE}/v1beta/models/{GEMINI_MODEL}:generateContent"
GEMINI_KEY_URL = "https://aistudio.google.com/apikey"

FREE_RUNS = 2
CHUNK_SIZE = 30_000          # characters per Gemini parsing chunk
MAX_DUMP_CHARS = 90_000      # cap per source (3 chunks) to respect free quotas
MAX_MENTIONS_FOR_SCORING = 400

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

FETCH_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
GEMINI_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

app = FastAPI(title="Brand Watch")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
security = HTTPBasic()


# ---------------------------------------------------------------- database

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS subscribers (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT NOT NULL,
               business_email TEXT NOT NULL,
               company_name TEXT NOT NULL,
               monitored_brand TEXT,
               created_at TEXT NOT NULL
           )"""
    )
    return conn


# ---------------------------------------------------------------- skills

def load_skill(name: str) -> str:
    """Load a skill file from disk at request time (never inlined in code)."""
    path = SKILLS_DIR / name / "SKILL.md"
    return path.read_text(encoding="utf-8")


def build_parser_prompt(profile: dict) -> str:
    """universal-parser + sentiment-calibration, placeholders substituted."""
    combined = (
        load_skill("universal-parser") + "\n\n" + load_skill("sentiment-calibration")
    )
    brand = profile.get("brand_name") or "the brand"
    aliases = []
    for key in ("legal_or_alt_names", "aliases_and_misspellings", "products"):
        value = profile.get(key) or []
        if isinstance(value, str):
            value = [value]
        aliases.extend(str(v) for v in value)
    alias_text = ", ".join(dict.fromkeys(aliases)) or "none known"
    industry = profile.get("industry") or "general business"
    return (
        combined.replace("[BRAND_NAME]", brand)
        .replace("[ALIASES]", alias_text)
        .replace("[USER_INDUSTRY]", industry)
    )


# ---------------------------------------------------------------- gemini

class GeminiError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# How long a key sits out after a 429 before we try it again, and how many
# consecutive 429s mark it exhausted-for-this-run. The cooldown keeps a healthy
# alternate key carrying the load while a limited key rests; the strike limit
# means a genuinely spent key (e.g. daily quota gone) is retired quickly rather
# than retried forever.
RATE_LIMIT_COOLDOWN = 6.0       # seconds a 429'd key rests before a retry
QUOTA_STRIKE_LIMIT = 3          # consecutive 429s on one key => drop it this run
MAX_WAIT_SLEEP = 6.0            # cap on how long a call blocks waiting for a key


class GeminiKeyPool:
    """A rotating pool of user-supplied Gemini keys.

    The free tier limits each key by requests-per-minute and requests-per-day.
    A single analysis fires many calls, so one key alone often hits the limit
    mid-run. The pool spreads calls across every key the user provides and,
    when a key returns 429, parks it on a short cooldown and rotates to the
    next — so the run keeps moving instead of stalling. Keys that are rejected
    outright (bad key) or that keep returning 429 are dropped for the run.
    """

    def __init__(self, keys: list[str]):
        # Preserve order, drop blanks and duplicates.
        self.keys = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))
        self._rr = 0
        self._cooldown: dict[int, float] = {}   # idx -> monotonic time usable again
        self._strikes: dict[int, int] = {}      # idx -> consecutive 429 count
        self._dead: set[int] = set()             # idx -> permanently out this run
        self._lock = asyncio.Lock()

    def has_keys(self) -> bool:
        return bool(self.keys)

    async def acquire(self) -> tuple[str, int | None, str | float]:
        """Pick a key to try now.

        Returns ("key", idx, key) when one is ready, ("wait", None, seconds)
        when all live keys are cooling down, or ("dead", None, 0) when every
        key is exhausted/invalid.
        """
        async with self._lock:
            now = time.monotonic()
            live = [i for i in range(len(self.keys)) if i not in self._dead]
            if not live:
                return ("dead", None, 0)
            ready = [i for i in live if self._cooldown.get(i, 0.0) <= now]
            if ready:
                idx = ready[self._rr % len(ready)]
                self._rr += 1
                return ("key", idx, self.keys[idx])
            wait = min(self._cooldown[i] for i in live) - now
            return ("wait", None, max(0.5, wait))

    async def report_ok(self, idx: int):
        async with self._lock:
            self._strikes[idx] = 0

    async def report_rate_limited(self, idx: int):
        async with self._lock:
            self._strikes[idx] = self._strikes.get(idx, 0) + 1
            if self._strikes[idx] >= QUOTA_STRIKE_LIMIT:
                # Looks like the daily quota, not a transient spike — retire it.
                self._dead.add(idx)
            else:
                self._cooldown[idx] = time.monotonic() + RATE_LIMIT_COOLDOWN

    async def report_transient(self, idx: int, seconds: float):
        async with self._lock:
            self._cooldown[idx] = time.monotonic() + seconds

    async def report_invalid(self, idx: int):
        async with self._lock:
            self._dead.add(idx)

    async def all_dead(self) -> bool:
        async with self._lock:
            return len(self._dead) >= len(self.keys)


async def call_gemini(client: httpx.AsyncClient, pool: "GeminiKeyPool",
                      system_prompt: str, user_text: str) -> str:
    if not pool.has_keys():
        raise GeminiError(
            "missing_key",
            f"No Gemini API key supplied. Get a free key at {GEMINI_KEY_URL} "
            "and paste it into Settings.",
        )
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }
    # Enough attempts to rotate through every key a few times and ride out
    # short cooldowns, without looping forever on a dead pool.
    max_attempts = max(6, len(pool.keys) * 4)
    saw_rate_limit = False
    for _ in range(max_attempts):
        status, idx, val = await pool.acquire()
        if status == "dead":
            raise GeminiError(
                "invalid_key",
                "Every Gemini key supplied was rejected. Double-check them, or "
                f"add a fresh free key at {GEMINI_KEY_URL}.",
            )
        if status == "wait":
            saw_rate_limit = True
            await asyncio.sleep(min(float(val), MAX_WAIT_SLEEP) + random.uniform(0, 1.0))
            continue
        key = str(val)
        try:
            resp = await client.post(
                GEMINI_URL, params={"key": key}, json=payload,
                timeout=GEMINI_TIMEOUT,
            )
        except httpx.HTTPError:
            await pool.report_transient(idx, 3.0)
            continue
        if resp.status_code == 429:
            saw_rate_limit = True
            await pool.report_rate_limited(idx)
            continue
        if resp.status_code in (400, 401, 403):
            body = resp.text[:500]
            if "API_KEY_INVALID" in body or "API key" in body or resp.status_code in (401, 403):
                await pool.report_invalid(idx)
                continue
            raise GeminiError("bad_request", f"Gemini rejected the request: {body}")
        if resp.status_code >= 500:
            await pool.report_transient(idx, 2.5)
            continue
        data = resp.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            block = (data.get("promptFeedback") or {}).get("blockReason")
            raise GeminiError(
                "empty_response",
                f"Gemini returned no usable text (block reason: {block or 'unknown'}).",
            )
        await pool.report_ok(idx)
        return text

    if await pool.all_dead():
        raise GeminiError(
            "invalid_key",
            "Every Gemini key supplied was rejected or exhausted. Add a fresh "
            f"free key at {GEMINI_KEY_URL}.",
        )
    if saw_rate_limit:
        suffix = ("Add a second free key in Settings so the analysis can fail "
                  "over between keys and avoid stalling."
                  if len(pool.keys) < 2 else
                  "Wait a minute for the per-minute limit to reset, then retry.")
        raise GeminiError(
            "quota",
            "All supplied Gemini keys are rate-limited or over their free quota. "
            + suffix,
        )
    raise GeminiError("server", "Gemini API kept failing across all supplied keys.")


def strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", text, re.S)
    if match:
        return match.group(1).strip()
    return text


def parse_json_payload(text: str):
    """Defensive JSON parse: strip fences, then fall back to bracket slicing."""
    cleaned = strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
    if starts:
        start = min(starts)
        closer = "}" if cleaned[start] == "{" else "]"
        end = cleaned.rfind(closer)
        if end > start:
            return json.loads(cleaned[start:end + 1])
    raise json.JSONDecodeError("no JSON object found", cleaned, 0)


async def gemini_json(client, pool, system_prompt, user_text):
    """Call Gemini expecting JSON; on parse failure retry once with a correction."""
    raw = await call_gemini(client, pool, system_prompt, user_text)
    try:
        return parse_json_payload(raw)
    except json.JSONDecodeError:
        correction = (
            user_text
            + "\n\nIMPORTANT CORRECTION: your previous reply was not valid JSON. "
              "Respond again with strictly valid JSON only — no markdown fences, "
              "no commentary, no trailing text."
        )
        raw = await call_gemini(client, pool, system_prompt, correction)
        return parse_json_payload(raw)


# ---------------------------------------------------------------- fetching

def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form",
                     "nav", "header", "footer", "aside", "template"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def normalise_url(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


async def fetch_page_text(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(
        url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-GB,en;q=0.9"},
        timeout=FETCH_TIMEOUT, follow_redirects=True,
    )
    resp.raise_for_status()
    return html_to_text(resp.text)


# ------------------------------------------------- search-engine bypass (X)

async def ddg_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get(
        "https://html.duckduckgo.com/html/", params={"q": query},
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-GB,en;q=0.9"},
        timeout=FETCH_TIMEOUT, follow_redirects=True,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for block in soup.select("div.result"):
        anchor = block.select_one("a.result__a")
        snippet = block.select_one(".result__snippet")
        if not anchor:
            continue
        href = anchor.get("href", "")
        if "uddg=" in href:
            try:
                href = unquote(parse_qs(urlparse(href).query)["uddg"][0])
            except (KeyError, IndexError):
                pass
        results.append({
            "title": anchor.get_text(" ", strip=True),
            "link": href,
            "snippet": snippet.get_text(" ", strip=True) if snippet else "",
        })
    return results


async def bing_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get(
        "https://www.bing.com/search", params={"q": query, "count": "20"},
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-GB,en;q=0.9"},
        timeout=FETCH_TIMEOUT, follow_redirects=True,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for block in soup.select("li.b_algo"):
        anchor = block.select_one("h2 a")
        snippet = block.select_one("p")
        if not anchor or not anchor.get("href"):
            continue
        results.append({
            "title": anchor.get_text(" ", strip=True),
            "link": anchor["href"],
            "snippet": snippet.get_text(" ", strip=True) if snippet else "",
        })
    return results


async def search_engine_dump(client: httpx.AsyncClient, query: str) -> str:
    """DuckDuckGo HTML endpoint first, Bing as fallback."""
    results = []
    try:
        results = await ddg_search(client, query)
    except Exception:
        results = []
    if not results:
        results = await bing_search(client, query)
    parts = [f'SEARCH ENGINE RESULTS for query: {query}']
    for i, r in enumerate(results, 1):
        parts.append(f"RESULT {i}\nTITLE: {r['title']}\nURL: {r['link']}\nSNIPPET: {r['snippet']}")
    return "\n\n".join(parts) if results else ""


# ---------------------------------------------------------------- reddit

def reddit_user_agent(username: str) -> str:
    handle = (username or "anonymous").lstrip("u/").strip()
    return f"web:brand-watch-monitor:v1.0 (by /u/{handle})"


async def reddit_dump(client: httpx.AsyncClient, creds: dict, profile: dict) -> str:
    """Search Reddit via the user's own OAuth app (client_credentials grant)."""
    ua = reddit_user_agent(creds.get("username", ""))
    token_resp = await client.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(creds["client_id"], creds["client_secret"]),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": ua},
        timeout=FETCH_TIMEOUT,
    )
    token_resp.raise_for_status()
    token = token_resp.json().get("access_token")
    if not token:
        raise RuntimeError("Reddit returned no access token — check the Client ID and Secret.")
    headers = {"Authorization": f"Bearer {token}", "User-Agent": ua}

    terms = [profile.get("brand_name") or ""]
    for alias in (profile.get("aliases_and_misspellings") or [])[:3]:
        terms.append(str(alias))
    terms = [t for t in dict.fromkeys(t.strip() for t in terms) if t]

    parts, seen_ids, top_permalinks = [], set(), []
    for term in terms:
        resp = await client.get(
            "https://oauth.reddit.com/search",
            params={"q": f'"{term}"', "limit": 25, "sort": "new", "t": "year",
                    "type": "link"},
            headers=headers, timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        for child in resp.json().get("data", {}).get("children", []):
            post = child.get("data", {})
            if post.get("id") in seen_ids:
                continue
            seen_ids.add(post.get("id"))
            created = post.get("created_utc")
            when = (datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat()
                    if created else "unknown")
            permalink = "https://www.reddit.com" + (post.get("permalink") or "")
            if len(top_permalinks) < 5 and post.get("num_comments", 0) > 0:
                top_permalinks.append(post.get("permalink"))
            parts.append(
                f"REDDIT POST in r/{post.get('subreddit')}\n"
                f"AUTHOR: u/{post.get('author')}\nDATE: {when}\n"
                f"URL: {permalink}\nTITLE: {post.get('title')}\n"
                f"BODY: {(post.get('selftext') or '')[:1500]}"
            )
        await asyncio.sleep(random.uniform(0.5, 1.2))

    # Pull comments from the most recent discussed threads for richer sentiment.
    for permalink in top_permalinks:
        try:
            resp = await client.get(
                f"https://oauth.reddit.com{permalink}.json",
                params={"limit": 30, "depth": 2},
                headers=headers, timeout=FETCH_TIMEOUT,
            )
            resp.raise_for_status()
            listing = resp.json()
            comments = listing[1].get("data", {}).get("children", []) if len(listing) > 1 else []
            for child in comments:
                cdata = child.get("data", {})
                body = cdata.get("body")
                if not body:
                    continue
                parts.append(
                    f"REDDIT COMMENT on https://www.reddit.com{permalink}\n"
                    f"AUTHOR: u/{cdata.get('author')}\nBODY: {body[:1200]}"
                )
        except Exception:
            continue
        await asyncio.sleep(random.uniform(0.5, 1.0))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- pipeline

def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    text = text[:MAX_DUMP_CHARS]
    chunks = []
    while text:
        if len(text) <= size:
            chunks.append(text)
            break
        cut = text.rfind("\n", max(0, size - 2000), size)
        if cut <= 0:
            cut = size
        chunks.append(text[:cut])
        text = text[cut:]
    return chunks


def normalise_mention(raw: dict, fallback_platform: str, fallback_link: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()
    if not text:
        return None
    sentiment = str(raw.get("sentiment") or "").lower()
    if sentiment not in ("positive", "neutral", "negative"):
        sentiment = "neutral"
    return {
        "platform": str(raw.get("platform") or fallback_platform or "unknown"),
        "text": text,
        "author_hint": raw.get("author_hint"),
        "date_hint": raw.get("date_hint"),
        "link": raw.get("link") or fallback_link,
        "sentiment": sentiment,
        "relevance": str(raw.get("relevance") or "confirmed"),
        "self_published": bool(raw.get("self_published", False)),
        "facet": str(raw.get("facet") or "customer"),
    }


def get_gemini_keys(body: dict) -> list[str]:
    """Collect every Gemini key the caller supplied, plus any server defaults.

    Accepts `gemini_keys` (a list) and/or the legacy single `gemini_key`, and
    the GEMINI_API_KEY env var (which may itself be comma-separated). Order is
    preserved; the pool dedupes and drops blanks.
    """
    collected: list[str] = []
    raw_list = body.get("gemini_keys")
    if isinstance(raw_list, list):
        collected.extend(str(k) for k in raw_list)
    if body.get("gemini_key"):
        collected.append(str(body["gemini_key"]))
    env = os.environ.get("GEMINI_API_KEY", "")
    collected.extend(env.split(","))
    return [k.strip() for k in collected if k and k.strip()]


def gate_state(request: Request) -> tuple[bool, int]:
    unlocked = request.cookies.get("bw_unlocked") == "1"
    try:
        used = int(request.cookies.get("usage_credits") or 0)
    except ValueError:
        used = 0
    return unlocked, used


# ---------------------------------------------------------------- routes

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/api/discover")
async def discover(request: Request):
    body = await request.json()
    pool = GeminiKeyPool(get_gemini_keys(body))
    website = normalise_url(body.get("website_url", ""))
    industry_override = (body.get("industry") or "").strip()
    if not website:
        return JSONResponse({"error": "Please enter your company website URL."}, status_code=422)

    async with httpx.AsyncClient() as client:
        try:
            home_text = await fetch_page_text(client, website)
        except Exception as exc:
            return JSONResponse(
                {"error": f"Could not fetch {website} — {exc}. Check the URL and try again."},
                status_code=502,
            )
        about_text = ""
        for path in ("/about", "/about-us"):
            try:
                about_text = await fetch_page_text(client, urljoin(website, path))
                break
            except Exception:
                continue

        user_text = (
            f"WEBSITE URL: {website}\n"
            + (f"USER-SUPPLIED INDUSTRY OVERRIDE (trust this over your own inference): {industry_override}\n"
               if industry_override else "")
            + f"\nHOMEPAGE TEXT DUMP:\n{home_text[:40000]}\n"
            + (f"\nABOUT PAGE TEXT DUMP:\n{about_text[:20000]}" if about_text else "")
        )
        try:
            result = await gemini_json(client, pool, load_skill("brand-discovery"), user_text)
        except GeminiError as exc:
            return JSONResponse({"error": exc.message, "code": exc.code}, status_code=502)
        except json.JSONDecodeError:
            return JSONResponse(
                {"error": "Gemini returned malformed JSON twice during discovery. Please retry."},
                status_code=502,
            )
    if not isinstance(result, dict) or "profile" not in result:
        return JSONResponse({"error": "Discovery returned an unexpected shape. Please retry."},
                            status_code=502)
    return JSONResponse(result)


@app.post("/api/run")
async def run_monitoring(request: Request):
    unlocked, used = gate_state(request)
    if not unlocked and used >= FREE_RUNS:
        return JSONResponse(
            {"error": "Free analysis credits used up.", "code": "gate"}, status_code=402
        )

    body = await request.json()
    pool = GeminiKeyPool(get_gemini_keys(body))
    profile = body.get("profile") or {}
    search_queries = [q for q in (body.get("search_queries") or []) if isinstance(q, str) and q.strip()]
    sources = body.get("sources") or []
    reddit_creds = body.get("reddit") or {}
    reddit_creds = {
        "client_id": (reddit_creds.get("client_id") or os.environ.get("REDDIT_CLIENT_ID", "")).strip(),
        "client_secret": (reddit_creds.get("client_secret") or os.environ.get("REDDIT_CLIENT_SECRET", "")).strip(),
        "username": (reddit_creds.get("username") or os.environ.get("REDDIT_USERNAME", "")).strip(),
    }
    if not profile.get("brand_name"):
        return JSONResponse({"error": "Run discovery first — no company profile supplied."},
                            status_code=422)

    statuses: list[dict] = []
    dumps: list[dict] = []  # {"source", "platform", "link", "text"}

    async with httpx.AsyncClient() as client:

        # ---- Step 3: ingestion (concurrent; one failure never kills the run)
        async def ingest_reddit():
            label = "Reddit (user OAuth)"
            if not (reddit_creds["client_id"] and reddit_creds["client_secret"]):
                statuses.append({"source": label, "status": "skipped",
                                 "detail": "No Reddit credentials supplied — relying on search-engine queries instead."})
                return
            try:
                text = await reddit_dump(client, reddit_creds, profile)
                if text:
                    dumps.append({"source": label, "platform": "Reddit",
                                  "link": "https://www.reddit.com", "text": text})
                    statuses.append({"source": label, "status": "ok", "chars": len(text)})
                else:
                    statuses.append({"source": label, "status": "empty",
                                     "detail": "0 results from Reddit search."})
            except Exception as exc:
                statuses.append({"source": label, "status": "error", "detail": str(exc)[:200]})

        async def ingest_searches():
            for query in search_queries[:8]:
                label = f"Search: {query}"
                try:
                    text = await search_engine_dump(client, query)
                    if text:
                        platform = "X" if "x.com" in query or "twitter" in query.lower() else "Search"
                        dumps.append({"source": label, "platform": platform,
                                      "link": "", "text": text})
                        statuses.append({"source": label, "status": "ok", "chars": len(text)})
                    else:
                        statuses.append({"source": label, "status": "empty",
                                         "detail": "0 indexed results found."})
                except Exception as exc:
                    statuses.append({"source": label, "status": "error", "detail": str(exc)[:200]})
                # Polite, randomised delay between search-engine requests.
                await asyncio.sleep(random.uniform(1.2, 2.8))

        async def ingest_url(source: dict):
            platform = str(source.get("platform") or "Custom URL")
            url = normalise_url(str(source.get("url") or ""))
            label = f"{platform} ({url})" if url else platform
            host = urlparse(url).netloc.lower()
            if any(d in host for d in ("reddit.com", "x.com", "twitter.com")):
                statuses.append({"source": label, "status": "skipped",
                                 "detail": "Covered by the Reddit API / search-engine bypass instead of a direct fetch."})
                return
            if not url:
                statuses.append({"source": label, "status": "error", "detail": "No URL."})
                return
            try:
                text = await fetch_page_text(client, url)
                if text:
                    dumps.append({"source": label, "platform": platform, "link": url, "text": text})
                    statuses.append({"source": label, "status": "ok", "chars": len(text)})
                else:
                    statuses.append({"source": label, "status": "empty",
                                     "detail": "Page fetched but contained no visible text."})
            except Exception as exc:
                statuses.append({"source": label, "status": "error", "detail": str(exc)[:200]})

        await asyncio.gather(
            ingest_reddit(),
            ingest_searches(),
            *(ingest_url(s) for s in sources),
        )

        # ---- Step 4: parsing (universal-parser + sentiment-calibration)
        parser_prompt = build_parser_prompt(profile)
        # Allow a little more in-flight parsing when several keys are available,
        # so the run actually benefits from the extra per-minute headroom.
        gemini_sem = asyncio.Semaphore(min(6, 2 + len(pool.keys)))
        mentions: list[dict] = []
        parse_failures: list[str] = []

        async def parse_chunk(dump: dict, chunk: str, index: int):
            user_text = (
                f"SOURCE: {dump['source']}\n"
                f"SOURCE URL: {dump['link'] or 'n/a'}\n\n"
                f"RAW TEXT DUMP:\n{chunk}"
            )
            async with gemini_sem:
                try:
                    parsed = await gemini_json(client, pool, parser_prompt, user_text)
                except GeminiError as exc:
                    if exc.code in ("invalid_key", "missing_key"):
                        raise
                    parse_failures.append(f"{dump['source']} chunk {index + 1}: {exc.message}")
                    return
                except json.JSONDecodeError:
                    parse_failures.append(
                        f"{dump['source']} chunk {index + 1}: invalid JSON after retry — chunk skipped."
                    )
                    return
            if isinstance(parsed, dict):
                parsed = [parsed]
            if not isinstance(parsed, list):
                parse_failures.append(f"{dump['source']} chunk {index + 1}: unexpected shape — skipped.")
                return
            for raw in parsed:
                mention = normalise_mention(raw, dump["platform"], dump["link"])
                if mention:
                    mentions.append(mention)

        try:
            await asyncio.gather(*(
                parse_chunk(dump, chunk, i)
                for dump in dumps
                for i, chunk in enumerate(chunk_text(dump["text"]))
            ))
        except GeminiError as exc:
            return JSONResponse({"error": exc.message, "code": exc.code}, status_code=502)

        # Deduplicate identical mention texts across overlapping sources.
        seen = set()
        unique_mentions = []
        for m in mentions:
            key = (m["platform"].lower(), m["text"][:160].lower())
            if key not in seen:
                seen.add(key)
                unique_mentions.append(m)
        mentions = unique_mentions

        # ---- Step 5: scoring (reputation-scoring skill)
        score = None
        if mentions:
            scoring_input = (
                "COMPANY PROFILE:\n" + json.dumps(profile, ensure_ascii=False)
                + "\n\nCLASSIFIED MENTIONS (universal-parser output):\n"
                + json.dumps(mentions[:MAX_MENTIONS_FOR_SCORING], ensure_ascii=False)
            )
            try:
                score = await gemini_json(client, pool,
                                          load_skill("reputation-scoring"), scoring_input)
            except GeminiError as exc:
                return JSONResponse({"error": exc.message, "code": exc.code}, status_code=502)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"error": "Gemini returned malformed JSON twice during scoring. Please retry the run."},
                    status_code=502,
                )

    payload = {
        "source_status": statuses,
        "mentions": mentions,
        "score": score,
        "parse_failures": parse_failures,
    }
    response = JSONResponse(payload)
    if not unlocked:
        response.set_cookie("usage_credits", str(used + 1),
                            max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


@app.post("/api/lead")
async def save_lead(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    email = (body.get("business_email") or "").strip().lower()
    company = (body.get("company_name") or "").strip()
    brand = (body.get("monitored_brand") or "").strip()
    if not (name and email and company):
        return JSONResponse({"error": "Name, business email and company name are all required."},
                            status_code=422)
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", email):
        return JSONResponse({"error": "That email address doesn't look valid."}, status_code=422)
    conn = db()
    try:
        conn.execute(
            "INSERT INTO subscribers (name, business_email, company_name, monitored_brand, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, email, company, brand, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    response = JSONResponse({"ok": True})
    response.set_cookie("bw_unlocked", "1", max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


@app.get("/api/credits")
async def credits(request: Request):
    unlocked, used = gate_state(request)
    return {"unlocked": unlocked, "used": used,
            "remaining": None if unlocked else max(0, FREE_RUNS - used)}


# ------------------------------------------------------------ blueprint zip

BLUEPRINT_FILES = [
    "app.py",
    "requirements.txt",
    "render.yaml",
    "Procfile",
    "templates/index.html",
    "static/app.js",
    "static/style.css",
    "static/vendor/chart.umd.min.js",
    "skills/brand-discovery/SKILL.md",
    "skills/universal-parser/SKILL.md",
    "skills/sentiment-calibration/SKILL.md",
    "skills/reputation-scoring/SKILL.md",
]

ENV_EXAMPLE = """# Optional server-side defaults. The dashboard also accepts keys pasted
# per-request, so all of these can stay empty for local use.
GEMINI_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USERNAME=
# Required only if you want to download leads from /export:
EXPORT_PASSWORD=
"""


@app.get("/api/blueprint")
async def blueprint(request: Request):
    unlocked, _ = gate_state(request)
    if not unlocked:
        return JSONResponse({"error": "Unlock the downloads first.", "code": "gate"},
                            status_code=402)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in BLUEPRINT_FILES:
            path = BASE_DIR / rel
            if path.exists():
                zf.writestr(f"brand-watch/{rel}", path.read_text(encoding="utf-8"))
        zf.writestr("brand-watch/.env.example", ENV_EXAMPLE)
        readme = (BASE_DIR / "blueprint" / "README.md").read_text(encoding="utf-8")
        zf.writestr("brand-watch/README.md", readme)
    buffer.seek(0)
    return StreamingResponse(
        buffer, media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="brand-watch-blueprint.zip"'},
    )


# ---------------------------------------------------------------- export

@app.get("/export")
async def export_csv(credentials: HTTPBasicCredentials = Depends(security)):
    password = os.environ.get("EXPORT_PASSWORD", "")
    if not password:
        return PlainTextResponse(
            "Export disabled: set the EXPORT_PASSWORD environment variable first.",
            status_code=503,
        )
    if not (compare_digest(credentials.username, "admin")
            and compare_digest(credentials.password, password)):
        return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})
    conn = db()
    try:
        rows = conn.execute(
            "SELECT id, name, business_email, company_name, monitored_brand, created_at "
            "FROM subscribers ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "name", "business_email", "company_name", "monitored_brand", "created_at"])
    writer.writerows(rows)
    return Response(
        out.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="brand-watch-leads.csv"'},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
