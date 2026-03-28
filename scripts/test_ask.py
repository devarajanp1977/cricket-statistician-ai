"""Quick test of the /api/ask endpoint."""
import json
import urllib.request

body = json.dumps({"question": "How many matches has India played in total?"}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8000/api/ask",
    data=body,
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=120)
data = json.loads(resp.read())

print("SQL:", data["sql"])
print("Rows:", data["rows"])
print("Answer:", data["answer"][:500])
print("Error:", data["error"])
