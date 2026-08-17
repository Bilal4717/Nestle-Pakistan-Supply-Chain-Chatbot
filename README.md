# Transport Intelligence — Nestlé Pakistan

A natural-language chatbot over Nestlé Pakistan's transport/logistics data. Ask plain-English questions about carriers, cost, lanes, and vehicle utilisation, and get real answers pulled live from the transport database — not a guess.

Built during the Supply Chain (Logistics/Transport) internship at Nestlé Pakistan, as a companion to the [Power BI cost-opportunity dashboard](#) built earlier in the internship.

## What it does

- Translates a plain-English question into SQL (never the other way around) using Gemini, then runs that SQL against a local SQLite database of ~62,700 real trip records
- Shows the exact SQL behind every answer — nothing is invented or hallucinated
- Separates **Dedicated** carriers (Nestlé-owned: BSL, IBS Logistics) from **Market/3PL** carriers wherever a carrier comparison is shown, since the two aren't directly comparable
- Handles multi-part questions (e.g. "top 5 carriers in Karachi, broken down by vehicle and business type, plus cheaper alternates") by generating multiple labeled queries when one query genuinely can't cover the whole answer
- Only suggests an alternate/cheaper carrier if it has a real track record on that lane (minimum trip count), not a one-off outlier
- Asks a clarifying question instead of guessing when a question is genuinely ambiguous (e.g. "best carrier" — cheapest, or highest volume?)
- Remembers the last 3 exchanges so follow-ups like "what about Lahore instead" work
- **Data privacy by design**: only the database schema and your question are ever sent to the API — actual trip data, costs, and figures never leave the machine. Results are computed locally and only optionally summarized by the model.

## Tech stack

- **Backend**: Flask (Python)
- **Database**: SQLite (local, read-only at query time)
- **LLM**: Google Gemini (`gemini-3.5-flash-lite`, free tier) for SQL generation only
- **Frontend**: single HTML/CSS/JS page, no framework

## Project structure

```
├── app.py                  # Flask routes + API endpoint
├── sql_agent.py             # NL -> SQL generation, validation, local execution
├── schema_prompt.py         # Schema description + few-shot examples fed to the model
├── nestle_transport.db      # SQLite database (trip-level transport records)
├── templates/
│   └── index.html            # Chat UI
├── api/
│   └── index.py               # Vercel serverless entrypoint (wraps the Flask app)
├── vercel.json               # Vercel routing config
├── requirements.txt
└── .env.example
```

## Running locally

1. Clone the repo and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Get a free Gemini API key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (no card required).
3. Copy `.env.example` to `.env` and paste your key in:
   ```
   GEMINI_API_KEY=your-key-here
   ```
4. Run it:
   ```bash
   python app.py
   ```
5. Open `http://127.0.0.1:5000`.

## Deploying to Vercel

1. Push this repo to GitHub.
2. Import the repo in Vercel.
3. Under **Project Settings → Environment Variables**, add `GEMINI_API_KEY` with your key. Do **not** put it in code or commit a `.env` file — `vercel.json`/`api/index.py` already handle routing, and Vercel injects the env var at runtime.
4. Deploy. Vercel's Python runtime picks up `api/index.py`, which wraps the existing Flask app — no code changes needed beyond what's already in this repo.

## Safety design

- Only `SELECT`/`WITH` (read-only) statements are ever executed — write/DDL keywords are blocked outright, regardless of what the model generates.
- The model never receives row-level data — only column names, types, and your question.

## Data source

Trip-level transport records (source plant, destination, carrier, vehicle size, cost, business type, dates) covering Nestlé Pakistan's F&B and Water logistics operations.
