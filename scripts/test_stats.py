"""Test regular stats query still works (non-scorecard)."""
import json
import urllib.request

body = json.dumps({
    "question": "Top 5 run scorers in Test cricket"
}).encode()

req = urllib.request.Request(
    "http://127.0.0.1:8080/api/ask",
    data=body,
    headers={"Content-Type": "application/json"},
)

print("Sending request...")
resp = urllib.request.urlopen(req, timeout=120)
data = json.loads(resp.read())

print("Display Hint:", data.get("display_hint"))
print("Has Sections:", data.get("sections") is not None)
print("Columns:", data.get("columns"))
print("Rows:", len(data.get("rows", [])))
print("Error:", data.get("error"))
print("Answer:", data.get("answer", "")[:200])

print()
print("=== Fielding Regression: IPL catches leader ===")
for question in [
    "Which fielder has caught the maximum catches in IPL and how many?",
    "Who has taken the most catches in IPL?",
]:
    body = json.dumps({"question": question}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8080/api/ask",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=120)
    fielding_data = json.loads(resp.read())
    print("Question:", question)
    print("Model:", fielding_data.get("model_used"))
    print("Rows:", fielding_data.get("rows"))
    print("Error:", fielding_data.get("error"))
    print("Answer:", fielding_data.get("answer", "")[:200])
    print("---")
