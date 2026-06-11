# Brand Watch — Source Code Blueprint

This folder contains the complete source code for **Brand Watch**, an AI-powered
brand sentiment monitor. It runs entirely on your own free API keys — there are
**no keys, passwords or customer records anywhere in this code**. Keys are read
strictly from environment variables (see `.env.example`) or pasted into the
dashboard at runtime.

## 🤖 How to get this running (no technical skills needed)

The easiest way is to hand this folder to an AI assistant such as Claude.
Upload the whole folder (or open it in Claude Code) and paste this message:

> Please set this Python project up for me on my computer. Specifically:
> 1. Check that Python 3.10 or newer is installed, and tell me how to install it if not.
> 2. Create a virtual environment in this folder and activate it.
> 3. Install the dependencies from requirements.txt.
> 4. Start the app with `python app.py` and tell me which address to open in my browser.
> 5. If anything fails, explain the error in plain English and fix it.

That's it. The assistant will do the rest and tell you when the app is running
(normally at http://localhost:8000).

## 🔑 What you'll need (all free)

1. **Google Gemini API key** — create one in about 30 seconds at
   https://aistudio.google.com/apikey
2. **Reddit API credentials (optional but recommended)** — go to
   https://www.reddit.com/prefs/apps, click "create another app", choose
   **script**, set the redirect URI to `http://localhost:8000`, and note the
   **Client ID** (under the app name) and **Client Secret**.

Paste these into the sidebar of the dashboard. They stay in your browser's
local storage and are sent only with each request — never saved on the server.

Alternatively, copy `.env.example` to `.env`, fill in the values, and export
them before starting the app; the server will use them as defaults.

## 🗂 What's in the folder

| Path | Purpose |
|---|---|
| `app.py` | The whole backend (FastAPI): discovery, ingestion, parsing, scoring, lead capture, exports |
| `templates/index.html` | The single-page dashboard |
| `static/` | Styles and dashboard logic |
| `skills/` | The four AI system prompts (the intelligence layer) — loaded from disk at runtime |
| `requirements.txt` | Python dependencies |
| `.env.example` | Optional environment variables |

## 🏃 Manual run (if you prefer doing it yourself)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://localhost:8000.

## 📤 Exporting captured leads

Leads entered through the unlock form are saved to `leads.db` (created
automatically). Set an `EXPORT_PASSWORD` environment variable, then visit
`/export` and log in with username `admin` and that password to download a CSV.
