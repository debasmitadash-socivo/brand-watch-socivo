"""
End-to-end funnel check with a mocked Gemini API — burns zero quota.

Starts a mock server (fake company homepage, fake review page, fake Gemini
endpoint) plus the real app pointed at it, then drives the whole funnel:

    discovery -> watchlist -> run x2 -> credit gate -> lead -> unlocked run
    -> blueprint download -> CSV export

The mock Gemini deliberately wraps discovery output in markdown fences and
returns garbage on every first parsing call, so the fence-stripper and the
correction-retry path are exercised on every run.

Usage:  python scripts/mock_e2e.py
Exits 0 and prints PASS on success; prints the first failure otherwise.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

BASE_DIR = Path(__file__).resolve().parent.parent
MOCK_PORT = 9100
APP_PORT = 9101
MOCK = f"http://127.0.0.1:{MOCK_PORT}"
APP = f"http://127.0.0.1:{APP_PORT}"

# ----------------------------------------------------------- mock content

HOMEPAGE = """<html><head><title>Acme Analytics</title></head><body>
<nav>Home About Pricing</nav>
<main><h1>Acme Analytics Ltd</h1>
<p>B2B revenue analytics for UK SaaS teams. Trusted by 200+ finance leaders.</p></main>
<script>console.log('stripped')</script>
<footer>Acme Analytics Ltd, London</footer></body></html>"""

REVIEWS = """<html><body><main>
<div class="review">Great product but support never replies, switching providers. — Jordan, 2 weeks ago</div>
<div class="review">Painless onboarding, our team adopted it without training. — Sam, March 2026</div>
<div class="review">Does the job, can't complain. — Alex</div>
</main></body></html>"""

DISCOVERY = {
    "profile": {
        "brand_name": "Acme Analytics",
        "legal_or_alt_names": ["Acme Analytics Ltd"],
        "aliases_and_misspellings": ["acme analytics", "acmeanalytics"],
        "industry": "SaaS / software",
        "business_model": "B2B",
        "what_they_do": "Revenue analytics for SaaS finance teams.",
        "target_customer": "Finance leaders at UK SaaS companies",
        "geography": ["UK"],
        "products": ["Acme Dashboard"],
    },
    "watchlist": [
        {"platform": "Trustpilot", "url": f"{MOCK}/reviews",
         "why": "Primary UK review platform", "priority": "high"},
    ],
    "search_queries": [],
    "confidence_notes": "Mock profile for pipeline testing.",
}

MENTIONS = [
    {"platform": "Trustpilot", "text": "Great product but support never replies, switching providers.",
     "author_hint": "Jordan", "date_hint": "2 weeks ago", "link": f"{MOCK}/reviews",
     "sentiment": "negative", "relevance": "confirmed", "self_published": False, "facet": "customer"},
    {"platform": "Trustpilot", "text": "Painless onboarding, our team adopted it without training.",
     "author_hint": "Sam", "date_hint": "March 2026", "link": f"{MOCK}/reviews",
     "sentiment": "positive", "relevance": "confirmed", "self_published": False, "facet": "customer"},
    {"platform": "Trustpilot", "text": "Does the job, can't complain.",
     "author_hint": "Alex", "date_hint": None, "link": f"{MOCK}/reviews",
     "sentiment": "positive", "relevance": "confirmed", "self_published": False, "facet": "customer"},
]

SCORE = {
    "score": 62, "band": "Healthy", "low_data": True,
    "breakdown": {"sentiment": {"positive": 2, "neutral": 0, "negative": 1},
                  "platforms": {"Trustpilot": 3}},
    "complaint_themes": [{"theme": "Slow support response", "count": 1,
                          "example": "support never replies, switching providers",
                          "platforms": ["Trustpilot"]}],
    "praise_themes": [{"theme": "Painless onboarding", "count": 1,
                       "example": "our team adopted it without training",
                       "platforms": ["Trustpilot"]}],
    "urgent_flags": ["Churn language in a Trustpilot review from the last month."],
    "quick_wins": ["Reply to the unanswered negative Trustpilot review."],
    "competitor_mentions": [],
    "employer_sentiment_note": "",
    "executive_summary": "Acme Analytics scores 62 (Healthy) on a very small sample.",
}

# ----------------------------------------------------------- mock server

mock = FastAPI()


@mock.get("/site")
async def site():
    return HTMLResponse(HOMEPAGE)


@mock.get("/reviews")
async def reviews():
    return HTMLResponse(REVIEWS)


@mock.post("/v1beta/models/{model}")
async def gemini(model: str, request: Request):
    body = await request.json()
    system_text = body["system_instruction"]["parts"][0]["text"]
    user_text = body["contents"][0]["parts"][0]["text"]
    if "Brand Discovery Agent" in system_text:
        # Fenced output: exercises the defensive fence-stripper.
        text = "```json\n" + json.dumps(DISCOVERY) + "\n```"
    elif "Reputation Scoring Engine" in system_text:
        text = json.dumps(SCORE)
    elif "IMPORTANT CORRECTION" in user_text:
        text = json.dumps(MENTIONS)
    else:
        # First parsing attempt is always garbage: exercises the retry path.
        text = "Sorry, I cannot produce structured output right now."
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def start_mock():
    config = uvicorn.Config(mock, host="127.0.0.1", port=MOCK_PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()


# ----------------------------------------------------------- the checks

FAILURES = []


def check(label: str, condition: bool, detail=""):
    print(("  ✔ " if condition else "  ✘ ") + label + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def main() -> int:
    start_mock()
    db_path = BASE_DIR / "leads.db"
    pre_existing_db = db_path.exists()
    env = dict(os.environ, GEMINI_API_BASE=MOCK, PORT=str(APP_PORT),
               EXPORT_PASSWORD="e2e-test-pw")
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=BASE_DIR, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=APP, timeout=60) as client:
            for _ in range(40):
                try:
                    client.get("/api/credits")
                    break
                except httpx.HTTPError:
                    time.sleep(0.25)

            print("Step 1 — fresh credits")
            data = client.get("/api/credits").json()
            check("starts with 2 free runs", data == {"unlocked": False, "used": 0, "remaining": 2}, data)

            print("Step 2 — discovery (fenced JSON from mock Gemini)")
            resp = client.post("/api/discover", json={
                "gemini_key": "mock-key", "website_url": f"{MOCK}/site", "industry": ""})
            check("discover returns 200", resp.status_code == 200, resp.text[:200])
            disc = resp.json()
            check("profile extracted through fence-stripper",
                  disc.get("profile", {}).get("brand_name") == "Acme Analytics")
            check("watchlist present", len(disc.get("watchlist", [])) == 1)

            run_body = {"gemini_key": "mock-key", "profile": disc["profile"],
                        "search_queries": [],
                        "sources": [{"platform": "Trustpilot", "url": f"{MOCK}/reviews"}]}

            print("Step 3 — run 1 (parse retry + scoring)")
            resp = client.post("/api/run", json=run_body)
            check("run 1 returns 200", resp.status_code == 200, resp.text[:200])
            result = resp.json()
            check("3 mentions extracted via correction-retry",
                  len(result.get("mentions", [])) == 3, result.get("parse_failures"))
            check("score produced", (result.get("score") or {}).get("score") == 62)
            check("source status reported ok",
                  any(s["status"] == "ok" for s in result["source_status"]))
            check("reddit skip is reported",
                  any(s["status"] == "skipped" for s in result["source_status"]))

            print("Step 4 — run 2, then the gate")
            check("run 2 returns 200",
                  client.post("/api/run", json=run_body).status_code == 200)
            resp = client.post("/api/run", json=run_body)
            check("run 3 blocked with 402 gate", resp.status_code == 402
                  and resp.json().get("code") == "gate", resp.status_code)
            check("blueprint locked before lead",
                  client.get("/api/blueprint").status_code == 402)

            print("Step 5 — lead capture unlocks everything")
            resp = client.post("/api/lead", json={
                "name": "E2E Tester", "business_email": "e2e@acme-analytics.co.uk",
                "company_name": "Acme Analytics", "monitored_brand": "Acme Analytics"})
            check("lead accepted", resp.status_code == 200, resp.text[:200])
            data = client.get("/api/credits").json()
            check("credits show unlocked", data.get("unlocked") is True, data)
            check("run 4 allowed after unlock",
                  client.post("/api/run", json=run_body).status_code == 200)

            print("Step 6 — downloads and export")
            resp = client.get("/api/blueprint")
            check("blueprint zip downloads", resp.status_code == 200
                  and resp.headers["content-type"] == "application/zip")
            import io
            import zipfile
            names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
            check("blueprint has README and skills",
                  "brand-watch/README.md" in names
                  and "brand-watch/skills/brand-discovery/SKILL.md" in names, names)
            check("blueprint contains no database or env file",
                  not any(n.endswith((".db", "/.env")) for n in names), names)
            resp = client.get("/export", auth=("admin", "e2e-test-pw"))
            check("CSV export contains the lead", resp.status_code == 200
                  and "e2e@acme-analytics.co.uk" in resp.text, resp.text[:200])
            check("export rejects wrong password",
                  client.get("/export", auth=("admin", "nope")).status_code == 401)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        if not pre_existing_db and db_path.exists():
            db_path.unlink()  # leave no test lead behind

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print("PASS — full funnel verified against the mocked Gemini API.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
