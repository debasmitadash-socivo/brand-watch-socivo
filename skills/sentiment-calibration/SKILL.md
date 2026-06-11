---
name: sentiment-calibration
description: The rules that make sentiment classification trustworthy — sarcasm, mixed feelings, B2B-specific language, UK understatement, and relevance filtering. Always appended to the universal-parser system prompt at runtime; never used alone.
---

# Sentiment Calibration Rules

Classification is strictly three-way: **positive, neutral, negative**. These rules govern the hard cases. When genuinely torn between two labels after applying them, choose neutral.

## 1. Sarcasm and irony

Internet text is heavy with sarcasm. Praise-shaped sentences describing failure are negative.

- "Wow, love it when the software crashes during a presentation" → **negative**
- "Brilliant. Third week waiting for a refund." → **negative**
- "10/10 would wait on hold for two hours again" → **negative**

Signals: praise vocabulary attached to objectively bad events, exaggerated enthusiasm about mundane failures, "thanks for nothing" constructions, slow clap energy.

## 2. UK understatement (critical for UK brands)

British reviewers under-state. Calibrate accordingly:

- "Not bad at all", "does the job", "can't complain", "pretty decent" → **positive** (mild but genuine)
- "A bit disappointing", "not quite what I hoped", "could be better", "leaves something to be desired" → **negative** (these are strong complaints in UK register)
- "Fine" / "okay I suppose" → **neutral**

## 3. Slang inversion

"Sick", "insane", "ridiculous(ly good)", "dangerous", "filthy", "goes hard" → **positive** when aimed at product quality. Read the object of the word, not the word.

## 4. Mixed sentiment

One comment, both directions. Decide by the reviewer's verdict, not the word count:

- Praise + dealbreaker ending ("Great product but support never replies, switching providers") → **negative**; the conclusion wins.
- Complaint + resolution ("Had billing issues but the team fixed it same day, impressed") → **positive**; resolution wins.
- Balanced trade-off with no verdict ("Powerful but expensive") → **neutral**.

## 5. B2B-specific language

B2B buyers complain in muted, procedural language. Treat the following as **negative** despite the calm tone: "we churned", "didn't renew", "moved to [competitor]", "couldn't get past procurement/security review", "support SLA was missed repeatedly", "pricing changed without notice", "feature has been on the roadmap for two years". Treat as **positive**: "renewed", "rolled it out company-wide", "our team adopted it without training", "CSM is excellent", "ROI within a quarter".

Feature requests phrased politely ("would love an API") are **neutral**, not negative, unless framed as a blocker.

## 6. Questions and pre-purchase chatter

"Anyone used [brand]? Thinking of switching to them" → **neutral** (but high value; keep it, it is a buying signal). A question wrapping a complaint ("Is anyone else's dashboard down AGAIN?") → **negative**.

## 7. Relevance filtering

Every mention must pass a relevance check against the company profile (industry, products, geography) before counting:

- Generic brand names (e.g. a company called "Beacon" or "Forge"): require contextual match — product terms, industry vocabulary, or a link to the brand's domain. Otherwise mark `"relevance": "excluded"`.
- Mentions of a different company with a similar name → `"excluded"`.
- Employee/job-seeker chatter (interview experiences, salaries) → keep, but tag `"facet": "employer"` so the scoring layer can weight it separately from customer sentiment.
- News about the company (funding, hires) with no opinion → **neutral**, `"facet": "news"`.

Default facet for ordinary customer opinion is `"customer"`.

## 8. What never changes a label

Star ratings visible in the dump are a hint, not the verdict — the text wins (a 4-star review whose text is a complaint is negative). Emoji are weak signals. Length is irrelevant: "rubbish" is as negative as three paragraphs.
