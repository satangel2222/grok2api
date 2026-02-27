"""
SSO Token NSFW Video Moderation Filter v2
==========================================
两阶段策略：
  Phase 1: 快速验证 token 有效性（GET 请求，<1秒/个）
  Phase 2: 对有效 token 做 NSFW 视频生成测试，检查 moderated 标志

用法:
  python filter_sso_v2.py             # 完整运行（先验证再测试）
  python filter_sso_v2.py --phase2    # 跳过验证，直接视频测试
  python filter_sso_v2.py --sample 50 # 只测 50 个 token
"""

import json
import time
import sys
import ssl
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Config ───
TOKEN_FILE = Path(r"D:\Users\Frank\AppData\Roaming\Telegram Desktop\tdata\temp_data\4600个sso.txt")
RESULTS_DIR = TOKEN_FILE.parent

GOOD_FILE = RESULTS_DIR / "sso_good_not_moderated.txt"
VALID_FILE = RESULTS_DIR / "sso_valid_tokens.txt"
MODERATE_FILE = RESULTS_DIR / "sso_moderated.txt"
INVALID_FILE = RESULTS_DIR / "sso_invalid_tokens.txt"
LOG_FILE = RESULTS_DIR / "sso_filter_log.txt"

PHASE1_WORKERS = 50   # 验证并发
PHASE2_WORKERS = 15   # 视频测试并发
VIDEO_TIMEOUT = 120   # 视频超时
AUTH_TIMEOUT = 15     # 认证超时

CTX = ssl.create_default_context()


# ─── Phase 1: Quick Auth Check ───
def check_auth(token: str, idx: int, total: int) -> tuple[str, bool, str]:
    """Quick check if token is valid via a lightweight Grok API call."""
    try:
        # Use the user settings endpoint - very lightweight
        req = urllib.request.Request(
            "https://grok.com/rest/app-chat/user-settings",
            headers={
                "Cookie": f"sso={token}; sso-rw={token}",
                "Origin": "https://grok.com",
                "Referer": "https://grok.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=AUTH_TIMEOUT, context=CTX) as resp:
            status = resp.status
            if status == 200:
                if idx % 100 == 0:
                    print(f"  [{idx}/{total}] ✓ valid")
                return token, True, ""
            return token, False, f"HTTP {status}"
    except urllib.error.HTTPError as e:
        if e.code == 401 or e.code == 403:
            return token, False, f"AUTH_{e.code}"
        if e.code == 429:
            # Rate limited - consider as valid (just throttled)
            return token, True, "rate_limited"
        return token, False, f"HTTP_{e.code}"
    except Exception as e:
        err = str(e)[:50]
        return token, False, err


def phase1_validate(tokens: list[str]) -> list[str]:
    """Phase 1: Fast auth validation."""
    print(f"\n{'='*60}")
    print(f"Phase 1: Validating {len(tokens)} tokens ({PHASE1_WORKERS} workers)")
    print(f"{'='*60}")
    
    valid = []
    invalid = []
    start = time.time()

    with ThreadPoolExecutor(max_workers=PHASE1_WORKERS) as ex:
        futs = {ex.submit(check_auth, t, i+1, len(tokens)): t for i, t in enumerate(tokens)}
        for f in as_completed(futs):
            token, is_valid, err = f.result()
            if is_valid:
                valid.append(token)
            else:
                invalid.append((token, err))

    elapsed = time.time() - start
    
    # Save valid tokens
    VALID_FILE.write_text("\n".join(valid) + "\n", encoding="utf-8")
    INVALID_FILE.write_text(
        "\n".join(f"{t}|{e}" for t, e in invalid) + "\n", encoding="utf-8"
    )

    print(f"\nPhase 1 done in {elapsed:.0f}s")
    print(f"  ✓ Valid: {len(valid)}")
    print(f"  ✗ Invalid: {len(invalid)}")
    print(f"  Saved: {VALID_FILE}")
    return valid


# ─── Phase 2: Video Moderation Test ───
def build_video_payload():
    return {
        "deviceEnvInfo": {
            "darkModeEnabled": False, "devicePixelRatio": 2,
            "screenWidth": 2056, "screenHeight": 1329,
            "viewportWidth": 2056, "viewportHeight": 1083,
        },
        "disableMemory": True, "disableSearch": False,
        "disableSelfHarmShortCircuit": True, "disableTextFollowUps": False,
        "enableImageGeneration": True, "enableImageStreaming": True,
        "enableNsfw": True, "enableSideBySide": True,
        "fileAttachments": [], "forceConcise": False, "forceSideBySide": False,
        "imageAttachments": [], "imageGenerationCount": 2,
        "isAsyncChat": False, "isReasoning": False,
        "message": "a beautiful woman slowly dancing --mode=extremely-crazy",
        "modelMode": None, "modelName": "grok-3",
        "responseMetadata": {
            "requestModelDetails": {"modelId": "grok-3"},
            "modelConfigOverride": {
                "modelMap": {
                    "videoGenModelConfig": {
                        "aspectRatio": "16:9", "resolutionName": "480p",
                        "videoLength": 6, "mode": "extremely-crazy",
                    }
                }
            },
        },
        "returnImageBytes": False, "returnRawGrokInXaiRequest": False,
        "sendFinalMetadata": True, "temporary": True,
        "toolOverrides": {"videoGen": True},
    }


def test_video_moderation(token: str, idx: int, total: int) -> dict:
    """Test a single token for NSFW video moderation."""
    result = {"token": token, "moderated": None, "error": None, "mode": None, "video_url": ""}
    
    try:
        payload = build_video_payload()
        data = json.dumps(payload).encode("utf-8")
        
        req = urllib.request.Request(
            "https://grok.com/rest/app-chat/conversations/new",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Cookie": f"sso={token}; sso-rw={token}",
                "Origin": "https://grok.com",
                "Referer": "https://grok.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            },
        )
        
        with urllib.request.urlopen(req, timeout=VIDEO_TIMEOUT, context=CTX) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                r = obj.get("result", {}).get("response", {})
                
                # Check error
                if r.get("errorType"):
                    result["error"] = r["errorType"]
                    print(f"  [{idx}/{total}] ⚠ error: {result['error'][:40]} | ...{token[-20:]}")
                    return result
                
                # Check video response
                vr = r.get("streamingVideoGenerationResponse")
                if vr and vr.get("progress") == 100:
                    result["moderated"] = vr.get("moderated")
                    result["mode"] = vr.get("mode")
                    result["video_url"] = vr.get("videoUrl", "")
                    
                    if not result["moderated"]:
                        print(f"  [{idx}/{total}] ✅ NOT MODERATED! mode={result['mode']} url={bool(result['video_url'])} | ...{token[-20:]}")
                    else:
                        print(f"  [{idx}/{total}] ❌ moderated | ...{token[-20:]}")
                    return result
        
        result["error"] = "no_video_100"
        print(f"  [{idx}/{total}] ⚠ no 100% progress | ...{token[-20:]}")
        
    except urllib.error.HTTPError as e:
        result["error"] = f"HTTP_{e.code}"
        print(f"  [{idx}/{total}] ❗ HTTP {e.code} | ...{token[-20:]}")
    except Exception as e:
        result["error"] = str(e)[:60]
        print(f"  [{idx}/{total}] ❗ {str(e)[:40]} | ...{token[-20:]}")
    
    return result


def phase2_video_test(tokens: list[str]) -> list[str]:
    """Phase 2: Test NSFW video moderation."""
    print(f"\n{'='*60}")
    print(f"Phase 2: Testing {len(tokens)} tokens for video moderation")
    print(f"  Workers: {PHASE2_WORKERS}, Timeout: {VIDEO_TIMEOUT}s")
    print(f"  Estimated time: {len(tokens) * 30 // PHASE2_WORKERS // 60:.0f}-{len(tokens) * 60 // PHASE2_WORKERS // 60:.0f} min")
    print(f"{'='*60}")
    
    good = []
    moderated = []
    errors = []
    start = time.time()
    
    with ThreadPoolExecutor(max_workers=PHASE2_WORKERS) as ex:
        futs = {ex.submit(test_video_moderation, t, i+1, len(tokens)): t for i, t in enumerate(tokens)}
        
        for f in as_completed(futs):
            r = f.result()
            if r["error"]:
                errors.append(r)
            elif r["moderated"] is False:
                good.append(r)
                # Save immediately
                with open(GOOD_FILE, "a", encoding="utf-8") as fout:
                    fout.write(f"{r['token']}\n")
            else:
                moderated.append(r)
            
            done = len(good) + len(moderated) + len(errors)
            if done % 20 == 0:
                el = time.time() - start
                rate = done / el if el > 0 else 0
                eta = (len(tokens) - done) / rate / 60 if rate > 0 else 0
                print(f"\n  --- {done}/{len(tokens)} | ✅{len(good)} ❌{len(moderated)} ⚠{len(errors)} | ETA: {eta:.1f}min ---\n")
    
    elapsed = time.time() - start
    
    # Save moderated
    MODERATE_FILE.write_text(
        "\n".join(r["token"] for r in moderated) + "\n", encoding="utf-8"
    )
    
    print(f"\n{'='*60}")
    print(f"Phase 2 done in {elapsed/60:.1f} min")
    print(f"  ✅ Good (NOT moderated): {len(good)} → {GOOD_FILE}")
    print(f"  ❌ Moderated:            {len(moderated)} → {MODERATE_FILE}")
    print(f"  ⚠ Errors:               {len(errors)}")
    print(f"{'='*60}")
    
    return [r["token"] for r in good]


# ─── Main ───
def main():
    args = sys.argv[1:]
    
    skip_phase1 = "--phase2" in args
    sample_size = None
    for a in args:
        if a.startswith("--sample"):
            sample_size = int(a.split("=")[1] if "=" in a else args[args.index(a) + 1])
    
    # Load tokens
    if skip_phase1 and VALID_FILE.exists():
        tokens = [l.strip() for l in VALID_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"Loaded {len(tokens)} pre-validated tokens from {VALID_FILE}")
    else:
        tokens = [l.strip() for l in TOKEN_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"Loaded {len(tokens)} tokens from {TOKEN_FILE}")
    
    if sample_size:
        tokens = tokens[:sample_size]
        print(f"Sampling first {sample_size} tokens")
    
    # Phase 1: Validate
    if not skip_phase1:
        tokens = phase1_validate(tokens)
        if not tokens:
            print("No valid tokens found!")
            return
    
    # Phase 2: Video test
    if GOOD_FILE.exists():
        GOOD_FILE.unlink()
    
    good = phase2_video_test(tokens)
    
    if good:
        print(f"\n🎉 Found {len(good)} tokens that bypass NSFW video moderation!")
    else:
        print(f"\n😔 No tokens bypass NSFW video moderation.")


if __name__ == "__main__":
    main()
