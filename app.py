"""
Local web app for the Nestlé Transport Intelligence chatbot.

Run with:
    pip install flask pandas google-generativeai
    python app.py
Then open http://127.0.0.1:5000 in your browser.
"""

from flask import Flask, request, jsonify, render_template
import sql_agent

app = Flask(__name__)

# Simple in-memory history for this local single-user app -- keeps the last 3
# exchanges (question + generated SQL only, never row data) so follow-up
# questions like "what about for Lahore instead" can be resolved.
CONVERSATION_HISTORY = []
MAX_HISTORY = 3

QUICK_QUESTIONS = [
    {"category": "Cost", "text": "What's the total transport cost split by business type?"},
    {"category": "Cost", "text": "Which zone has the highest average cost per trip?"},
    {"category": "Carriers", "text": "Which carrier is cheapest for 40FT trips to Karachi?"},
    {"category": "Carriers", "text": "How does dedicated carrier spend compare to 3PL spend?"},
    {"category": "Utilisation", "text": "Show me average vehicle utilisation by vehicle size"},
    {"category": "Contracts", "text": "What percentage of trips use Contractual vs Alternate Vendor clause?"},
]


@app.route("/")
def index():
    return render_template("index.html", quick_questions=QUICK_QUESTIONS)


@app.route("/api/ask", methods=["POST"])
def api_ask():
    question = (request.json or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "Ask something first."}), 400

    try:
        raw = sql_agent.generate_sql(question, history=CONVERSATION_HISTORY)
    except Exception as e:
        return jsonify({"error": f"Couldn't reach the model: {e}"}), 500

    if raw.upper().startswith("CLARIFY:"):
        clarify_msg = raw.split(":", 1)[1].strip()
        return jsonify({"clarify": clarify_msg})

    if "CANNOT_ANSWER" in raw.upper() and "QUERY:" not in raw.upper():
        return jsonify({
            "sql": raw,
            "error": "That's outside what the transport data can answer. Try asking about cost, carriers, lanes, vehicle size, or utilisation.",
        })

    query_blocks = sql_agent.parse_queries(raw)
    results = []
    for label, sql in query_blocks:
        try:
            df = sql_agent.run_sql(sql)
        except sql_agent.UnsafeSQLError as e:
            return jsonify({"sql": raw, "error": f"Blocked an unsafe query: {e}"}), 400
        except Exception as e:
            return jsonify({"sql": raw, "error": f"One part of that query didn't run cleanly ({label or 'result'}): {e}"}), 500

        results.append({
            "label": label,
            "sql": sql,
            "columns": list(df.columns),
            "rows": df.head(50).values.tolist(),
            "row_count": len(df),
        })

    # Remember this turn for follow-up context (question + SQL only, no results/data)
    CONVERSATION_HISTORY.append({"question": question, "sql": raw})
    del CONVERSATION_HISTORY[:-MAX_HISTORY]

    return jsonify({"results": results})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    CONVERSATION_HISTORY.clear()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
