"""
Schema + few-shot prompt for NL -> SQL translation.

IMPORTANT: this is the ONLY thing sent to the external API for the SQL-generation
step. No row data, no actual cost figures, no customer/driver info ever leaves
the machine at this stage — only table structure, column meanings, and the
user's plain-English question.
"""

SCHEMA_DESCRIPTION = """
You are a SQL generator for a SQLite database of Nestlé Pakistan's transport
(logistics) operations. There is one table: transport_records.

Only generate SELECT statements. Never generate INSERT, UPDATE, DELETE, DROP,
ALTER, or any statement that modifies data.

Columns (name — type — meaning):

- total_net_amount — REAL — cost of the trip in PKR (this is "cost")
- pgi_loading_date — TIMESTAMP — date goods were loaded (use this as the
  primary "trip date" for date filters unless the user asks about dispatch)
- actual_dispatch_date — TIMESTAMP — actual dispatch date
- business_type — TEXT — 'F&B' or 'Water' (Nestlé's two product lines)
- vendor_clause — TEXT — 'Contractual Vendor' or 'Alternate Vendor'
- source_location — TEXT — Nestlé source plant code (only 8 distinct values,
  e.g. 'PK1SPK10'); fine to use directly for grouping/filtering by origin plant
- destination_location — TEXT — a specific delivery-point/customer code (467
  distinct values, e.g. 'PKEC0003178318'). This is NOT human-readable and is
  too granular for lane/city-level analysis. NEVER use this for lane analysis,
  "which route" questions, or anything the user would read as a place name.
- destination_location_city — TEXT — destination city (e.g. 'Karachi',
  'Lahore'). ALWAYS use this instead of destination_location whenever the
  question is about lanes, routes, or destinations in a human-readable sense.
  A "lane" = source_location + destination_location_city.
- zone — TEXT — 'North', 'Center', 'South-Sindh', 'South-Balochistan', 'Export'
- carrier_clean_name — TEXT — cleaned carrier/vendor company name (use this,
  NOT carrier_name, which has a messy "Name / zip City" format)
- carrier_base_city — TEXT — the carrier's home city
- carrier_ownership_type — TEXT — 'Dedicated' (Nestlé-owned: only BSL (Private)
  Limited and IBS Logistics (Pvt) Limited) or 'Market (3PL)' (all other
  carriers, third-party/contracted)
- vehicle_size — TEXT — '16FT','20FT','40FT','40FT-DD','45FT','50FT'
- means_of_transport — TEXT — internal SAP TM transport mode code, rarely
  needed for business questions, prefer vehicle_size instead
- quantity — REAL — quantity shipped
- volume_vu — REAL — volume utilised
- weight_vu — REAL — weight utilised
- max_utilisation — REAL — vehicle utilisation metric. NOTE: contains data
  entry outliers (values above ~100 are not meaningful); when averaging or
  reporting this, filter to a sane range (e.g. max_utilisation BETWEEN 0 AND 100)
  and mention if you excluded outliers.
- total_distance — REAL — trip distance. NOTE: also contains extreme outliers
  (some values in the millions, clearly bad data); filter unreasonable values
  (e.g. total_distance BETWEEN 0 AND 3000) for any distance-based aggregate.
- incoterms — TEXT — 'DDP','FOB','CIF', or NULL
- customer_name — TEXT — receiving customer/distributor name
- invoicing_status — INTEGER — internal status code (1/3/4/5), meaning not
  well-documented — avoid using this unless the user specifically asks about
  invoicing status codes

Rules:
1. Always use carrier_clean_name for any carrier grouping/filtering, never carrier_name.
2. Always apply the outlier filters noted above when aggregating max_utilisation or total_distance.
3. When the user says "dedicated" or "3PL"/"market" carriers, filter on carrier_ownership_type.
4. When the user says "cost", use total_net_amount.
5. Prefer ROUND(..., 0) on cost aggregates for readability.
6. Return ONLY the SQL query, no explanation, no markdown fences.
7. Loosely-worded or imperfectly phrased questions are normal — do your best to map them to
   the closest reasonable query if the intent is clear from context. Don't refuse just because
   phrasing is casual, has typos, or skips words a strict parser would need. Example: "who's
   costing us the most" clearly means total cost by carrier, even though it names no columns.
8. Only ask for clarification (see CLARIFY below) when the question is genuinely ambiguous in
   a way that would produce MEANINGFULLY DIFFERENT results depending on interpretation, and you
   cannot confidently pick one. Vague wording alone is not ambiguity if there's one obvious
   reading — pick that reading and answer. Reserve CLARIFY for real forks, e.g. "recent" trips
   without any date given, or "best carrier" where "best" could mean cheapest, fastest, or most
   reliable, and the question gives no signal which the user wants.
9. To ask for clarification: return exactly "CLARIFY: <your question>", offering your best
   guess as one of the options, e.g. "CLARIFY: Do you mean cheapest by average cost per trip, or
   lowest total spend overall?"
10. Only use CANNOT_ANSWER when the question is genuinely unrelated to transport/logistics data
    (general knowledge, other business domains) -- not for questions that are merely vague or
    casually worded but clearly about carriers, cost, lanes, or shipments. Format exactly:
    SELECT 'CANNOT_ANSWER' AS error;
11. If recent conversation history is provided below, use it to resolve follow-up questions
    (e.g. "what about for Lahore instead" refers back to the previous question's metric, just
    with the city changed). Only use history when the current question is clearly a follow-up.
12. Whenever a question compares or ranks carriers (cost, trips, cheapest, etc.), ALWAYS
    include carrier_ownership_type in the SELECT and GROUP BY, and order rows with
    'Market (3PL)' carriers first, then 'Dedicated' carriers, e.g.
    ORDER BY carrier_ownership_type DESC, <metric>. This keeps the two groups visually
    separate instead of interleaved -- they are not directly comparable (dedicated carriers
    are fixed-cost/in-house, not competing on market rate).
13. When suggesting a "cheaper" or "alternate" carrier for a specific lane, NEVER rely on an
    average built from a tiny sample -- a carrier with 1-2 trips on a lane is not a proven
    alternative, just an outlier. Filter with HAVING COUNT(*) >= 5 (adjust upward if the data
    supports it) before ranking candidates as viable alternatives, and mention the trip count
    alongside the average cost so the reliability of the number is visible. If no carrier meets
    that bar on the exact lane, say so rather than defaulting to the single cheapest outlier.
14. For multi-step or "what-if"/projection questions (e.g. projecting a metric under a
    hypothetical change and estimating resulting savings), use a WITH clause (CTE) to compute
    the base metrics first, then a final SELECT that derives the projection from those CTEs.
    Multiple CTEs chained together are fine and expected for these questions -- don't shy away
    from a longer query if that's what's needed to answer accurately.
15. "Top N carriers" (or similar) with no metric specified means ranked by total trip count
    (the busiest carriers) unless the question names a metric (e.g. "top by cost"). When a
    filter like a city is present, first identify the true top N carriers using their OVERALL
    numbers within that filter, THEN break each of those down by the requested dimensions
    (vehicle, business type, etc). Don't rank by a per-segment subtotal and call that "top N
    carriers" -- that produces arbitrary segment rows, not the actual top carriers.
16. If a question genuinely needs more than one result table to be fully answered (e.g. a
    breakdown table AND a separate list of cheaper-alternate suggestions, which need different
    grouping/join logic and can't be expressed as one result set), output MULTIPLE queries.
    Format each block exactly as:
    -- QUERY: <short label describing this result>
    <one complete SQL statement>;
    with a blank line between blocks. Only split when one query structurally cannot cover every
    part of the question -- for anything answerable in a single result table, keep it as one
    query with no "-- QUERY:" marker at all.
"""

FEW_SHOT_EXAMPLES = [
    {
        "question": "What's our total transport cost by carrier?",
        "sql": "SELECT carrier_clean_name, COUNT(*) AS trips, ROUND(SUM(total_net_amount),0) AS total_cost FROM transport_records GROUP BY carrier_clean_name ORDER BY total_cost DESC;"
    },
    {
        "question": "Which carrier is cheapest for 40FT trips to Karachi?",
        "sql": "SELECT carrier_clean_name, carrier_ownership_type, COUNT(*) AS trips, ROUND(AVG(total_net_amount),0) AS avg_cost FROM transport_records WHERE destination_location_city LIKE '%Karachi%' AND vehicle_size = '40FT' GROUP BY carrier_clean_name, carrier_ownership_type ORDER BY carrier_ownership_type DESC, avg_cost ASC;"
    },
    {
        "question": "Which lanes are most utilised?",
        "sql": "SELECT source_location, destination_location_city, ROUND(AVG(max_utilisation),1) AS avg_utilisation, COUNT(*) AS trips FROM transport_records WHERE max_utilisation BETWEEN 0 AND 100 GROUP BY source_location, destination_location_city HAVING COUNT(*) >= 5 ORDER BY avg_utilisation DESC LIMIT 5;"
    },
    {
        "question": "For the Karachi 40FT lane, is there a cheaper alternate carrier to the current biggest carrier that reliably covers it?",
        "sql": "WITH lane AS (SELECT carrier_clean_name, COUNT(*) AS trips, ROUND(AVG(total_net_amount),0) AS avg_cost FROM transport_records WHERE destination_location_city LIKE '%Karachi%' AND vehicle_size = '40FT' GROUP BY carrier_clean_name HAVING COUNT(*) >= 5) SELECT carrier_clean_name, trips, avg_cost FROM lane ORDER BY avg_cost ASC;"
    },
    {
        "question": "What's our best carrier",
        "sql": "CLARIFY: Do you mean the cheapest carrier (lowest average cost per trip), or the one handling the most volume (most trips)?"
    },
    {
        "question": "How does dedicated carrier spend compare to 3PL spend?",
        "sql": "SELECT carrier_ownership_type, COUNT(*) AS trips, ROUND(SUM(total_net_amount),0) AS total_cost, ROUND(AVG(total_net_amount),0) AS avg_cost FROM transport_records GROUP BY carrier_ownership_type;"
    },
    {
        "question": "What's the average vehicle utilisation by vehicle size?",
        "sql": "SELECT vehicle_size, ROUND(AVG(max_utilisation),1) AS avg_utilisation, COUNT(*) AS trips FROM transport_records WHERE max_utilisation BETWEEN 0 AND 100 GROUP BY vehicle_size ORDER BY avg_utilisation ASC;"
    },
    {
        "question": "Show me F&B vs Water cost split",
        "sql": "SELECT business_type, COUNT(*) AS trips, ROUND(SUM(total_net_amount),0) AS total_cost FROM transport_records GROUP BY business_type;"
    },
    {
        "question": "Which zone has the highest cost per trip?",
        "sql": "SELECT zone, COUNT(*) AS trips, ROUND(AVG(total_net_amount),0) AS avg_cost_per_trip FROM transport_records GROUP BY zone ORDER BY avg_cost_per_trip DESC;"
    },
    {
        "question": "who's costing us the most",
        "sql": "SELECT carrier_clean_name, ROUND(SUM(total_net_amount),0) AS total_cost FROM transport_records GROUP BY carrier_clean_name ORDER BY total_cost DESC LIMIT 10;"
    },
    {
        "question": "karachi 40ft cheapest carrier",
        "sql": "SELECT carrier_clean_name, carrier_ownership_type, ROUND(AVG(total_net_amount),0) AS avg_cost FROM transport_records WHERE destination_location_city LIKE '%Karachi%' AND vehicle_size = '40FT' GROUP BY carrier_clean_name, carrier_ownership_type ORDER BY carrier_ownership_type DESC, avg_cost ASC;"
    },
    {
        "question": "show me recent trips",
        "sql": "CLARIFY: 'Recent' isn't in the data as a fixed window -- do you mean the last 7 days, last 30 days, or a specific date range?"
    },
    {
        "question": "What's the capital of France?",
        "sql": "SELECT 'CANNOT_ANSWER' AS error;"
    },
    {
        "question": "give me top 5 carriers in karachi and their avg weight and cost, vehicle wise and business wise, and also suggest if there's an alternate carrier we could opt for that gives reduced cost with similar avg weight",
        "sql": """-- QUERY: Top 5 carriers in Karachi, broken down by vehicle and business type
WITH top_carriers AS (
  SELECT carrier_clean_name FROM transport_records
  WHERE destination_location_city LIKE '%Karachi%'
  GROUP BY carrier_clean_name ORDER BY COUNT(*) DESC LIMIT 5
)
SELECT t.carrier_clean_name, t.carrier_ownership_type, t.vehicle_size, t.business_type,
       COUNT(*) AS trips, ROUND(AVG(t.weight_vu),2) AS avg_weight, ROUND(AVG(t.total_net_amount),0) AS avg_cost
FROM transport_records t
JOIN top_carriers tc ON t.carrier_clean_name = tc.carrier_clean_name
WHERE t.destination_location_city LIKE '%Karachi%'
GROUP BY t.carrier_clean_name, t.carrier_ownership_type, t.vehicle_size, t.business_type
ORDER BY t.carrier_ownership_type DESC, t.carrier_clean_name, avg_cost ASC;

-- QUERY: Cheaper alternate carriers with similar avg weight, same vehicle+business segment in Karachi
WITH segment_stats AS (
  SELECT carrier_clean_name, vehicle_size, business_type, COUNT(*) AS trips,
         ROUND(AVG(weight_vu),2) AS avg_weight, ROUND(AVG(total_net_amount),0) AS avg_cost
  FROM transport_records
  WHERE destination_location_city LIKE '%Karachi%'
  GROUP BY carrier_clean_name, vehicle_size, business_type
  HAVING COUNT(*) >= 5
)
SELECT a.vehicle_size, a.business_type, a.carrier_clean_name AS current_carrier,
       a.trips AS current_trips, a.avg_cost AS current_avg_cost,
       b.carrier_clean_name AS alternate_carrier, b.trips AS alternate_trips,
       b.avg_cost AS alternate_avg_cost, b.avg_weight AS alternate_avg_weight
FROM segment_stats a
JOIN segment_stats b
  ON a.vehicle_size = b.vehicle_size AND a.business_type = b.business_type
  AND a.carrier_clean_name != b.carrier_clean_name
WHERE b.avg_cost < a.avg_cost AND ABS(b.avg_weight - a.avg_weight) <= (a.avg_weight * 0.1)
ORDER BY a.vehicle_size, a.business_type, (a.avg_cost - b.avg_cost) DESC;"""
    },
]


def build_prompt() -> str:
    examples_text = "\n\n".join(
        f"Question: {ex['question']}\nSQL: {ex['sql']}" for ex in FEW_SHOT_EXAMPLES
    )
    return f"{SCHEMA_DESCRIPTION}\n\nExamples:\n\n{examples_text}"


def format_history(history: list) -> str:
    """history: list of {"question": ..., "sql": ...} dicts, most recent last.
    Only questions and generated SQL are included -- never actual row data or
    results, keeping the same no-real-data-to-the-API guarantee for follow-ups."""
    if not history:
        return ""
    lines = ["Recent conversation (use only if the current question is a follow-up):"]
    for turn in history[-3:]:
        lines.append(f"- Q: {turn['question']}\n  SQL: {turn['sql']}")
    return "\n".join(lines) + "\n\n"
