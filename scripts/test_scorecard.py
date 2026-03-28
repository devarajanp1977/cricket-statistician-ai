"""Test the scorecard feature end-to-end."""
import json
import urllib.request

# Test 1: Direct scorecard query
print("=== Test 1: Direct scorecard ===")
body = json.dumps({
    "question": "Show me the scorecard of the match where Kohli scored 254"
}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8080/api/ask",
    data=body, headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=120)
data = json.loads(resp.read())
print("Display Hint:", data.get("display_hint"))
print("Has Sections:", data.get("sections") is not None)
if data.get("sections"):
    print("Innings:", len(data["sections"].get("innings", [])))
print("Error:", data.get("error"))
print()

# Test 2: Follow-up style — "show me the scorecard of the highest..."
# Simulates the user's failed flow: match returned as full row from kaggle_matches
print("=== Test 2: Follow-up scorecard (simulating full match row) ===")
body2 = json.dumps({
    "question": "Show me the scorecard of the 2014 Adelaide Test between India and Australia",
    "history": [
        {
            "question": "How does Kohli perform in 4th innings?",
            "context_summary": "Kohli 4th-innings Test batting via Kaggle scorecards",
            "sql": "SELECT * FROM kaggle_batting WHERE batsman IN (SELECT kaggle_player_id FROM player_map WHERE player_name ILIKE '%Kohli%')"
        }
    ]
}).encode()
req2 = urllib.request.Request(
    "http://127.0.0.1:8080/api/ask",
    data=body2, headers={"Content-Type": "application/json"},
)
resp2 = urllib.request.urlopen(req2, timeout=120)
data2 = json.loads(resp2.read())
print("SQL:", data2.get("sql", "")[:200])
print("Display Hint:", data2.get("display_hint"))
print("Has Sections:", data2.get("sections") is not None)
print("Columns:", data2.get("columns", [])[:3])
if data2.get("sections"):
    mi = data2["sections"].get("match_info", {})
    print("Match:", mi.get("title"))
    print("Result:", mi.get("result"))
    print("Innings:", len(data2["sections"].get("innings", [])))
else:
    print("NO SECTIONS - rows:", len(data2.get("rows", [])))
print("Error:", data2.get("error"))
