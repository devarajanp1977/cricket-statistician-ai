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

# Test 3: Latest Test scorecard should render real player names, not unresolved IDs
print()
print("=== Test 3: Latest Test scorecard regression ===")
body3 = json.dumps({
    "question": "Give me the scorecard of the latest test match played"
}).encode()
req3 = urllib.request.Request(
    "http://127.0.0.1:8080/api/ask",
    data=body3, headers={"Content-Type": "application/json"},
)
resp3 = urllib.request.urlopen(req3, timeout=120)
data3 = json.loads(resp3.read())
print("Display Hint:", data3.get("display_hint"))
print("Has Sections:", data3.get("sections") is not None)
if data3.get("sections"):
    first_innings = data3["sections"].get("innings", [{}])[0]
    first_batter = None
    if first_innings.get("batting", {}).get("rows"):
        first_batter = first_innings["batting"]["rows"][0][0]
    print("Match:", data3["sections"].get("match_info", {}).get("title"))
    print("First batter:", first_batter)
    print("Unresolved IDs Present:", any(
        str(row[0]).strip() == "-1"
        for innings in data3["sections"].get("innings", [])
        for row in innings.get("batting", {}).get("rows", [])
    ))
print("Cached:", data3.get("cached", False))
print("Error:", data3.get("error"))

# Test 4: Latest men's Test should not return the latest women's Test
print()
print("=== Test 4: Latest men's Test scorecard ===")
body4 = json.dumps({
    "question": "Give me the scorecard of the latest men's test match played"
}).encode()
req4 = urllib.request.Request(
    "http://127.0.0.1:8080/api/ask",
    data=body4, headers={"Content-Type": "application/json"},
)
resp4 = urllib.request.urlopen(req4, timeout=120)
data4 = json.loads(resp4.read())
sections4 = data4.get("sections") or {}
match4 = sections4.get("match_info", {})
print("SQL:", data4.get("sql"))
print("Match:", match4.get("title"))
print("Result:", match4.get("result"))
print("Start:", match4.get("start_date"))
print("Error:", data4.get("error"))

# Test 5: Match-specific follow-up should stay on the previous ODI match
print()
print("=== Test 5: ODI player-of-the-match follow-up ===")
body5a = json.dumps({
    "question": "Give me the scorecard of the latest ODI match played"
}).encode()
req5a = urllib.request.Request(
    "http://127.0.0.1:8080/api/ask",
    data=body5a, headers={"Content-Type": "application/json"},
)
resp5a = urllib.request.urlopen(req5a, timeout=120)
data5a = json.loads(resp5a.read())
history5 = [{
    "question": "Give me the scorecard of the latest ODI match played",
    "context_summary": data5a.get("context_summary") or "",
    "sql": data5a.get("sql") or "",
}]
body5b = json.dumps({
    "question": "Who was the man of the match in this?",
    "history": history5,
}).encode()
req5b = urllib.request.Request(
    "http://127.0.0.1:8080/api/ask",
    data=body5b, headers={"Content-Type": "application/json"},
)
resp5b = urllib.request.urlopen(req5b, timeout=120)
data5b = json.loads(resp5b.read())
print("Previous SQL:", data5a.get("sql"))
print("Follow-up SQL:", data5b.get("sql"))
print("Answer:", data5b.get("answer"))
print("Rows:", data5b.get("rows"))
print("Model Used:", data5b.get("model_used"))
print("Error:", data5b.get("error"))

# Test 6: Latest T20 International scorecard should resolve to IT20 and build sections
print()
print("=== Test 6: Latest T20 International scorecard ===")
body6 = json.dumps({
    "question": "Give me the scorecard of the latest T20 International match played"
}).encode()
req6 = urllib.request.Request(
    "http://127.0.0.1:8080/api/ask",
    data=body6, headers={"Content-Type": "application/json"},
)
resp6 = urllib.request.urlopen(req6, timeout=120)
data6 = json.loads(resp6.read())
sections6 = data6.get("sections") or {}
match6 = sections6.get("match_info", {})
print("SQL:", data6.get("sql"))
print("Match:", match6.get("title"))
print("Start:", match6.get("start_date"))
print("Sections:", bool(data6.get("sections")))
print("Model Used:", data6.get("model_used"))
print("Error:", data6.get("error"))

# Test 7: Latest men's Test scorecard should resolve deterministically
print()
print("=== Test 7: Latest men's Test scorecard deterministic ===")
body7 = json.dumps({
    "question": "Give me the scorecard of the latest men's test match"
}).encode()
req7 = urllib.request.Request(
    "http://127.0.0.1:8080/api/ask",
    data=body7, headers={"Content-Type": "application/json"},
)
resp7 = urllib.request.urlopen(req7, timeout=120)
data7 = json.loads(resp7.read())
sections7 = data7.get("sections") or {}
match7 = sections7.get("match_info", {})
print("SQL:", data7.get("sql"))
print("Match:", match7.get("title"))
print("Start:", match7.get("start_date"))
print("Sections:", bool(data7.get("sections")))
print("Model Used:", data7.get("model_used"))
print("Error:", data7.get("error"))
