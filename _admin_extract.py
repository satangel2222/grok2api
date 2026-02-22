"""以管理员权限运行 - 使用 chromium_based() 正确API"""
import rookiepy
import json
import os
import urllib.request

CHROME_DATA = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
PROFILES = ["Default", "Profile 1", "Profile 2", "Profile 3"]
DOMAINS = [".grok.com", ".x.com", "grok.com", "x.com"]
GROK2API_URL = "http://localhost:8001"
ADMIN_PWD = "grok2api"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "data", "extracted_tokens.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "data", "extract_log.txt")

log_lines = []
def log(msg):
    print(msg)
    log_lines.append(msg)

def save_log():
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

key_path = os.path.join(CHROME_DATA, "Local State")
tokens = []

log(f"Key: {os.path.exists(key_path)}")

for profile in PROFILES:
    db_net = os.path.join(CHROME_DATA, profile, "Network", "Cookies")
    db_old = os.path.join(CHROME_DATA, profile, "Cookies")
    db_path = db_net if os.path.exists(db_net) else db_old

    log(f"\n--- {profile} (db={os.path.exists(db_path)}) ---")
    if not os.path.exists(db_path):
        continue

    try:
        cookies = rookiepy.chromium_based(
            key_path=key_path,
            db_path=db_path,
            domains=DOMAINS
        )
        log(f"  cookies: {len(cookies)}")
        for c in cookies:
            if c.get("name") == "sso" and c.get("value"):
                val = c["value"]
                dom = c.get("domain", "")
                if val not in [t["token"] for t in tokens]:
                    tokens.append({"token": val, "domain": dom, "profile": profile})
                    log(f"  [+] SSO! {dom} len={len(val)} {val[:25]}...")
    except Exception as e:
        log(f"  ERR: {e}")

log(f"\nTotal: {len(tokens)} tokens")

with open(OUTPUT_FILE, "w") as f:
    json.dump(tokens, f, indent=2)
log(f"Saved: {OUTPUT_FILE}")

# Import to grok2api
if tokens:
    log(f"\nImporting to {GROK2API_URL}...")
    try:
        tlist = [{"token": t["token"], "status": "active", "quota": 80, "tags": [], "note": f"auto-{t['profile']}"} for t in tokens]
        data = json.dumps({"ssoBasic": tlist}).encode()
        req = urllib.request.Request(f"{GROK2API_URL}/v1/admin/tokens", data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {ADMIN_PWD}"}, method="POST")
        resp = urllib.request.urlopen(req, timeout=10)
        log(f"  API: {resp.status} {resp.read().decode()[:200]}")
    except Exception as e:
        log(f"  API err: {e}")

save_log()
log("\nDone! Press Enter...")
input()
