"""
SSO Token NSFW Video Moderation Filter
======================================
直接调用 Grok 的 app-chat API 测试每个 SSO token，
检查 NSFW 视频生成时 moderated 是 true 还是 false。

策略：用 text-to-video（不需要先生成图片），快速检查 moderated 标志。
"""

import asyncio
import json
import time
import sys
import os
from pathlib import Path

# We'll use urllib since we can't guarantee httpx/aiohttp
import urllib.request
import urllib.error
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Grok API endpoint
GROK_API = "https://grok.com/rest/app-chat/conversations/new"

# Output files
RESULTS_DIR = Path(r"D:\Users\Frank\AppData\Roaming\Telegram Desktop\tdata\temp_data")
GOOD_TOKENS_FILE = RESULTS_DIR / "good_tokens_not_moderated.txt"
BAD_TOKENS_FILE = RESULTS_DIR / "bad_tokens_moderated.txt"
ERROR_TOKENS_FILE = RESULTS_DIR / "error_tokens.txt"
PROGRESS_FILE = RESULTS_DIR / "filter_progress.txt"

# Concurrency
MAX_WORKERS = 20  # Concurrent requests
TIMEOUT = 90      # Seconds per request


def build_payload():
    """Build a minimal video generation payload."""
    return {
        "deviceEnvInfo": {
            "darkModeEnabled": False,
            "devicePixelRatio": 2,
            "screenWidth": 2056,
            "screenHeight": 1329,
            "viewportWidth": 2056,
            "viewportHeight": 1083,
        },
        "disableMemory": True,
        "disableSearch": False,
        "disableSelfHarmShortCircuit": True,
        "disableTextFollowUps": False,
        "enableImageGeneration": True,
        "enableImageStreaming": True,
        "enableNsfw": True,
        "enableSideBySide": True,
        "fileAttachments": [],
        "forceConcise": False,
        "forceSideBySide": False,
        "imageAttachments": [],
        "imageGenerationCount": 2,
        "isAsyncChat": False,
        "isReasoning": False,
        "message": "nude woman dancing --mode=extremely-crazy",
        "modelMode": None,
        "modelName": "grok-3",
        "responseMetadata": {
            "requestModelDetails": {"modelId": "grok-3"},
            "modelConfigOverride": {
                "modelMap": {
                    "videoGenModelConfig": {
                        "aspectRatio": "16:9",
                        "resolutionName": "480p",
                        "videoLength": 6,
                        "mode": "extremely-crazy",
                    }
                }
            },
        },
        "returnImageBytes": False,
        "returnRawGrokInXaiRequest": False,
        "sendFinalMetadata": True,
        "temporary": True,
        "toolOverrides": {"videoGen": True},
    }


def test_token(token: str, idx: int, total: int) -> dict:
    """Test a single SSO token for video moderation."""
    result = {
        "token": token[:30] + "...",
        "token_full": token,
        "moderated": None,
        "error": None,
        "mode": None,
        "has_video_url": False,
    }

    try:
        payload = build_payload()
        data = json.dumps(payload).encode("utf-8")

        # Build request with SSO cookie
        req = urllib.request.Request(
            GROK_API,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Cookie": f"sso={token}; sso-rw={token}",
                "Origin": "https://grok.com",
                "Referer": "https://grok.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/event-stream",
            },
        )

        ctx = ssl.create_default_context()

        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                r = obj.get("result", {}).get("response", {})

                # Check for video response
                video_resp = r.get("streamingVideoGenerationResponse")
                if video_resp and video_resp.get("progress") == 100:
                    result["moderated"] = video_resp.get("moderated")
                    result["mode"] = video_resp.get("mode")
                    result["has_video_url"] = bool(video_resp.get("videoUrl"))
                    
                    status = "✅ NOT moderated" if not result["moderated"] else "❌ moderated"
                    print(f"[{idx}/{total}] {status} | mode={result['mode']} | url={result['has_video_url']} | {result['token']}")
                    return result

                # Check for auth errors
                if r.get("errorType"):
                    result["error"] = r.get("errorType")
                    print(f"[{idx}/{total}] ⚠️ Error: {result['error']} | {result['token']}")
                    return result

        # If we get here, no video response found (maybe rejected before generation)
        result["error"] = "no_video_response"
        print(f"[{idx}/{total}] ⚠️ No video response | {result['token']}")

    except urllib.error.HTTPError as e:
        result["error"] = f"HTTP {e.code}"
        if e.code == 401:
            print(f"[{idx}/{total}] 🔑 Auth failed (401) | {result['token']}")
        elif e.code == 429:
            print(f"[{idx}/{total}] ⏳ Rate limited (429) | {result['token']}")
        else:
            print(f"[{idx}/{total}] ❗ HTTP {e.code} | {result['token']}")
    except Exception as e:
        result["error"] = str(e)[:80]
        print(f"[{idx}/{total}] ❗ {result['error'][:60]} | {result['token']}")

    return result


def main():
    # Read tokens
    token_file = Path(r"D:\Users\Frank\AppData\Roaming\Telegram Desktop\tdata\temp_data\4600个sso.txt")
    tokens = [line.strip() for line in token_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    total = len(tokens)
    print(f"Loaded {total} tokens. Starting filter with {MAX_WORKERS} concurrent workers...")
    print(f"Results will be saved to: {RESULTS_DIR}")
    print("=" * 80)

    good_tokens = []
    bad_tokens = []
    error_tokens = []
    
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(test_token, token, i + 1, total): token
            for i, token in enumerate(tokens)
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                if result["error"]:
                    error_tokens.append(result)
                elif result["moderated"] is False:
                    good_tokens.append(result)
                    # Save immediately
                    with open(GOOD_TOKENS_FILE, "a", encoding="utf-8") as f:
                        f.write(f"{result['token_full']}\n")
                else:
                    bad_tokens.append(result)
            except Exception as e:
                print(f"Future error: {e}")

            # Progress update every 50 tokens
            done = len(good_tokens) + len(bad_tokens) + len(error_tokens)
            if done % 50 == 0:
                elapsed = time.time() - start_time
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(f"\n--- Progress: {done}/{total} ({done*100//total}%) | Good: {len(good_tokens)} | Bad: {len(bad_tokens)} | Error: {len(error_tokens)} | ETA: {eta:.0f}s ---\n")

    # Final save
    elapsed = time.time() - start_time
    
    with open(BAD_TOKENS_FILE, "w", encoding="utf-8") as f:
        for r in bad_tokens:
            f.write(f"{r['token_full']}\n")

    with open(ERROR_TOKENS_FILE, "w", encoding="utf-8") as f:
        for r in error_tokens:
            f.write(f"{r['token_full']}|{r['error']}\n")

    print("\n" + "=" * 80)
    print(f"DONE in {elapsed:.1f}s")
    print(f"  ✅ Good (not moderated): {len(good_tokens)} → {GOOD_TOKENS_FILE}")
    print(f"  ❌ Bad (moderated):      {len(bad_tokens)} → {BAD_TOKENS_FILE}")
    print(f"  ⚠️ Errors:               {len(error_tokens)} → {ERROR_TOKENS_FILE}")
    print("=" * 80)


if __name__ == "__main__":
    # Clear previous good tokens file
    if GOOD_TOKENS_FILE.exists():
        GOOD_TOKENS_FILE.unlink()
    main()
