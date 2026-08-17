"""
NL -> SQL -> local execution pipeline.

Data-privacy design:
  - Step 1 (generate_sql): sends ONLY the schema description + the user's
    question to the API. No row data, no actual figures.
  - Step 2 (run_sql): executes the returned SQL against the LOCAL SQLite
    file. Real numbers never leave this machine unless you explicitly choose
    to send the result back to the API for phrasing (see summarize_result,
    optional and off by default).

Requires: pip install google-generativeai pandas
Requires: GEMINI_API_KEY environment variable set (get a free key at
https://aistudio.google.com/apikey -- no credit card needed).
"""

import os
import re
import sqlite3
import pandas as pd
from schema_prompt import SCHEMA_DESCRIPTION, build_prompt, format_history

DB_PATH = os.path.join(os.path.dirname(__file__), "nestle_transport.db")
MODEL = "gemini-3.5-flash-lite"  # current GA model as of Aug 2026, free tier

# Loads GEMINI_API_KEY from a local .env file if python-dotenv is installed
# (for local dev) -- on Vercel, the key comes from the dashboard's
# Environment Variables setting instead, no .env file needed there.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Basic safety net: only allow statements that start with SELECT and contain
# no write/DDL keywords anywhere in the string.
FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|PRAGMA|TRUNCATE)\b",
    re.IGNORECASE,
)


class UnsafeSQLError(Exception):
    pass


QUERY_MARKER = re.compile(r"--\s*QUERY:\s*(.+)", re.IGNORECASE)


def parse_queries(raw: str) -> list:
    """Split a model response into [(label, sql), ...] pairs.
    If no '-- QUERY:' markers are present, returns a single (None, raw) pair
    for backward compatibility with simple single-query answers."""
    parts = QUERY_MARKER.split(raw)
    if len(parts) == 1:
        return [(None, raw.strip())]
    pairs = []
    for i in range(1, len(parts), 2):
        label = parts[i].strip()
        sql = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if sql:
            pairs.append((label, sql))
    return pairs if pairs else [(None, raw.strip())]


def _pick_working_model(genai) -> str:
    """If MODEL has been retired, ask the API what's actually available on this
    key and pick a flash model instead of hardcoding names that keep changing."""
    candidates = [m.name.split("/")[-1] for m in genai.list_models()
                  if "generateContent" in m.supported_generation_methods]
    # prefer a "flash" model (fast/cheap), fall back to first available
    for name in candidates:
        if "flash" in name.lower() and "image" not in name.lower():
            return name
    return candidates[0] if candidates else MODEL


def generate_sql(question: str, history: list = None, _retried: bool = False) -> str:
    """Send schema + question (+ optional recent history) to the API, get back a
    SQL string, a CLARIFY: message, or CANNOT_ANSWER. No row data sent."""
    import google.generativeai as genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Locally: create a .env file with "
            "GEMINI_API_KEY=your-key-here. On Vercel: add it under "
            "Project Settings -> Environment Variables."
        )

    genai.configure(api_key=api_key)
    system_prompt = build_prompt()
    history_text = format_history(history or [])

    model_name = MODEL
    try:
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
        )
        resp = model.generate_content(
            f"{history_text}Question: {question}\nSQL:",
            generation_config=genai.types.GenerationConfig(
                temperature=0,  # deterministic SQL, no creative variation
                max_output_tokens=1200,  # complex CTE queries need real headroom
            ),
        )
    except Exception as e:
        if "404" in str(e) and not _retried:
            # MODEL name got retired -- ask the API what's live and retry once
            working_model = _pick_working_model(genai)
            print(f"[note] '{model_name}' unavailable, auto-switching to '{working_model}'")
            globals()["MODEL"] = working_model
            return generate_sql(question, history=history, _retried=True)
        raise

    sql = resp.text.strip()
    # strip accidental markdown fences
    sql = re.sub(r"^```sql\s*|\s*```$", "", sql, flags=re.IGNORECASE).strip()
    return sql


def validate_sql(sql: str) -> None:
    stripped = sql.strip().rstrip(";")
    upper = stripped.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise UnsafeSQLError(f"Only SELECT/WITH (read-only) statements are allowed. Got: {sql!r}")
    if FORBIDDEN_KEYWORDS.search(sql):
        raise UnsafeSQLError(f"Query contains a forbidden keyword: {sql!r}")


def run_sql(sql: str) -> pd.DataFrame:
    validate_sql(sql)
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(sql, conn)
    finally:
        conn.close()
    return df


def ask(question: str, verbose: bool = True) -> pd.DataFrame:
    """End-to-end: question -> SQL -> local execution -> DataFrame result."""
    sql = generate_sql(question)
    if verbose:
        print(f"[generated SQL] {sql}")

    if "CANNOT_ANSWER" in sql.upper():
        raise ValueError("The question couldn't be mapped to this schema.")

    return run_sql(sql)


if __name__ == "__main__":
    # Quick manual test harness. Requires GEMINI_API_KEY to be set.
    test_questions = [
        "Which carrier is cheapest for 40FT trips to Karachi?",
        "How does dedicated carrier spend compare to 3PL spend?",
        "What's the total transport cost split by business type?",
        "Which zone has the highest average cost per trip?",
        "Show me average vehicle utilisation by vehicle size",
        "How many trips has TSD (Private) Limited done in total, and what's their total cost?",
        "Compare average cost per trip for dedicated vs market carriers specifically in Karachi",
        "Which carrier has done the most trips overall?",
        "What percentage of trips use Contractual Vendor vs Alternate Vendor clause?",
        "What's the capital of France?",  # should return CANNOT_ANSWER, not hallucinate
    ]
    for q in test_questions:
        print("\nQ:", q)
        try:
            result = ask(q)
            print(result.to_string(index=False))
        except Exception as e:
            print("Error:", e)
