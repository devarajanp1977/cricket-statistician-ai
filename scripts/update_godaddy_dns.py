import json
import os
import sys
import urllib.request


def get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_public_ip() -> str:
    req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode().strip()


def godaddy_request(method: str, url: str, api_key: str, api_secret: str, payload=None):
    headers = {
        "Authorization": f"sso-key {api_key}:{api_secret}",
        "Content-Type": "application/json",
        "User-Agent": "cricket-statistician-ai-ddns/1.0",
    }
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def main() -> int:
    api_key = get_env("GODADDY_API_KEY")
    api_secret = get_env("GODADDY_API_SECRET")
    domain = get_env("GODADDY_DOMAIN")
    record_name = os.getenv("GODADDY_RECORD_NAME", "@")
    ttl = int(os.getenv("GODADDY_TTL", "600"))

    public_ip = get_public_ip()
    base = "https://api.godaddy.com/v1/domains"
    record_url = f"{base}/{domain}/records/A/{record_name}"

    current = godaddy_request("GET", record_url, api_key, api_secret) or []
    current_ip = current[0].get("data") if current else None

    if current_ip == public_ip:
        print(f"No change: {record_name}.{domain} already points to {public_ip}")
        return 0

    payload = [{"data": public_ip, "ttl": ttl}]
    godaddy_request("PUT", record_url, api_key, api_secret, payload)
    print(f"Updated {record_name}.{domain} from {current_ip or '<unset>'} to {public_ip}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)