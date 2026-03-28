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
