"""Extract tokens from grok2api's token.json and test them for moderation."""
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

TOKEN_JSON = Path(r"e:\zimagen\grok2api\data\token.json")
API_BASE = "https://grok2api.fly.dev"
RESULTS_DIR = Path(r"D:\Users\Frank\AppData\Roaming\Telegram Desktop\tdata\temp_data")
GOOD_FILE = RESULTS_DIR / "grok2api_good_tokens.txt"
BAD_FILE = RESULTS_DIR / "grok2api_moderated_tokens.txt"
TIMEOUT = 180
WORKERS = 5

def test_token(token, idx, total):
    result = {"token": token, "moderated": None, "error": None, "mode": None}
    try:
        data = json.dumps({
            "token": token,
            "prompt": "a beautiful woman slowly dancing --mode=extremely-crazy",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{API_BASE}/v1/admin/test-moderation",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            res = json.loads(resp.read().decode())
        result["moderated"] = res.get("moderated")
        result["mode"] = res.get("mode")
        result["error"] = res.get("error")
        
        if result["error"]:
            print(f"[{idx}/{total}] ⚠ {result['error'][:50]} | ...{token[-16:]}")
        elif result["moderated"] is False:
            print(f"[{idx}/{total}] ✅ NOT MODERATED | ...{token[-16:]}")
        else:
            print(f"[{idx}/{total}] ❌ moderated | ...{token[-16:]}")
    except Exception as e:
        result["error"] = str(e)[:80]
        print(f"[{idx}/{total}] ❗ {str(e)[:40]} | ...{token[-16:]}")
    return result

def main():
    # Extract tokens
    with open(TOKEN_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    tokens = []
    for pool_name, pool_list in data.items():
        if isinstance(pool_list, list):
            for entry in pool_list:
                if isinstance(entry, dict) and entry.get("token"):
                    tokens.append(entry["token"])
    
    total = len(tokens)
    print(f"Extracted {total} tokens from {TOKEN_JSON}")
    print(f"Workers: {WORKERS}, Timeout: {TIMEOUT}s")
    print("=" * 60)
    
    for f in [GOOD_FILE, BAD_FILE]:
        if f.exists():
            f.unlink()
    
    good, bad, errors = [], [], []
    start = time.time()
    
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(test_token, t, i+1, total): t for i, t in enumerate(tokens)}
        for f in as_completed(futs):
            r = f.result()
            if r["error"]:
                errors.append(r)
            elif r["moderated"] is False:
                good.append(r)
                with open(GOOD_FILE, "a", encoding="utf-8") as fout:
                    fout.write(f"{r['token']}\n")
            else:
                bad.append(r)
                with open(BAD_FILE, "a", encoding="utf-8") as fout:
                    fout.write(f"{r['token']}\n")
    
    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"DONE in {elapsed/60:.1f} min")
    print(f"  ✅ 超级权限 (NOT moderated): {len(good)}")
    print(f"  ❌ 被审核 (moderated):       {len(bad)}")
    print(f"  ⚠ 错误:                     {len(errors)}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
