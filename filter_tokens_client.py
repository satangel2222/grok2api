"""
Batch SSO Token Moderation Filter - Client
===========================================
通过 grok2api.fly.dev 的 /v1/admin/test-moderation 端点
批量测试 SSO token 的 NSFW 视频 moderation 状态。

用法:
  python filter_tokens_client.py                # 全量筛选
  python filter_tokens_client.py --sample 20    # 先测 20 个
"""

import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Config ───
API_BASE = "https://grok2api.fly.dev"
APP_KEY = "grok2api"  # admin auth
API_KEY = "zimagenart@worldno1"  # public auth (fallback)

TOKEN_FILE = Path(r"D:\Users\Frank\AppData\Roaming\Telegram Desktop\tdata\temp_data\4600个sso.txt")
RESULTS_DIR = TOKEN_FILE.parent
GOOD_FILE = RESULTS_DIR / "sso_good_not_moderated.txt"
MODERATED_FILE = RESULTS_DIR / "sso_moderated.txt"
ERROR_FILE = RESULTS_DIR / "sso_errors.txt"

WORKERS = 10       # 并发数（别太高免得打爆 Fly.io）
TIMEOUT = 180      # 每个 token 测试超时（视频生成需要 30-60 秒）


def test_token(token: str, idx: int, total: int) -> dict:
    """Test one token via admin endpoint."""
    result = {"token": token, "moderated": None, "mode": None, "video_url": None, "error": None}
    
    try:
        data = json.dumps({
            "token": token,
            "prompt": "a beautiful woman slowly dancing --mode=extremely-crazy",
        }).encode("utf-8")
        
        req = urllib.request.Request(
            f"{API_BASE}/v1/admin/test-moderation",
            data=data,
            headers={
                "Content-Type": "application/json",
            },
        )
        
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            res = json.loads(resp.read().decode())
        
        result["moderated"] = res.get("moderated")
        result["mode"] = res.get("mode")
        result["video_url"] = res.get("video_url")
        result["error"] = res.get("error")
        
        if result["error"]:
            print(f"[{idx}/{total}] ⚠ {result['error'][:50]} | ...{token[-16:]}")
        elif result["moderated"] is False:
            print(f"[{idx}/{total}] ✅ NOT MODERATED! mode={result['mode']} | ...{token[-16:]}")
        elif result["moderated"] is True:
            print(f"[{idx}/{total}] ❌ moderated | ...{token[-16:]}")
        else:
            print(f"[{idx}/{total}] ❓ unknown | ...{token[-16:]}")
            
    except urllib.error.HTTPError as e:
        result["error"] = f"HTTP_{e.code}"
        print(f"[{idx}/{total}] ❗ HTTP {e.code} | ...{token[-16:]}")
    except Exception as e:
        result["error"] = str(e)[:80]
        print(f"[{idx}/{total}] ❗ {str(e)[:40]} | ...{token[-16:]}")
    
    return result


def main():
    # Parse args
    sample = None
    if "--sample" in sys.argv:
        idx = sys.argv.index("--sample")
        sample = int(sys.argv[idx + 1])
    
    # Load tokens
    tokens = [l.strip() for l in TOKEN_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    total_all = len(tokens)
    
    if sample:
        tokens = tokens[:sample]
    
    total = len(tokens)
    print(f"Loaded {total_all} tokens, testing {total}")
    print(f"Workers: {WORKERS}, Timeout: {TIMEOUT}s")
    print(f"Estimated: {total * 45 // WORKERS // 60:.0f}-{total * 90 // WORKERS // 60:.0f} min")
    print(f"Results → {RESULTS_DIR}")
    print("=" * 60)
    
    # Clear previous
    for f in [GOOD_FILE, MODERATED_FILE, ERROR_FILE]:
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
                # Save immediately
                with open(GOOD_FILE, "a", encoding="utf-8") as fout:
                    fout.write(f"{r['token']}\n")
            elif r["moderated"] is True:
                bad.append(r)
            else:
                errors.append(r)
            
            done = len(good) + len(bad) + len(errors)
            if done % 20 == 0 and done > 0:
                el = time.time() - start
                rate = done / el if el > 0 else 0
                eta = (total - done) / rate / 60 if rate > 0 else 0
                print(f"\n--- {done}/{total} | ✅{len(good)} ❌{len(bad)} ⚠{len(errors)} | {el/60:.1f}min | ETA:{eta:.1f}min ---\n")
    
    elapsed = time.time() - start
    
    # Save results
    with open(MODERATED_FILE, "w", encoding="utf-8") as f:
        for r in bad:
            f.write(f"{r['token']}\n")
    with open(ERROR_FILE, "w", encoding="utf-8") as f:
        for r in errors:
            f.write(f"{r['token']}|{r['error']}\n")
    
    print(f"\n{'='*60}")
    print(f"DONE in {elapsed/60:.1f} min")
    print(f"  ✅ Good (NOT moderated): {len(good)} → {GOOD_FILE}")
    print(f"  ❌ Moderated:            {len(bad)} → {MODERATED_FILE}")
    print(f"  ⚠ Errors:               {len(errors)} → {ERROR_FILE}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
