"""
方案: 依次启动 Chrome profile 带 --remote-debugging-port
然后通过 CDP 连接提取 SSO cookie
"""
import asyncio
import json
import os
import subprocess
import time
import urllib.request
import signal

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_DATA = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
PROFILES = ["Default", "Profile 1", "Profile 2", "Profile 3"]
DEBUG_PORT = 9333
GROK2API_URL = "http://localhost:8001"
ADMIN_PWD = "grok2api"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


async def extract_from_profile(profile_name):
    """启动Chrome profile → CDP连接 → 提取cookie → 关闭"""
    from playwright.async_api import async_playwright

    print(f"  Launching Chrome --profile-directory=\"{profile_name}\" --remote-debugging-port={DEBUG_PORT}")

    # 启动 Chrome
    proc = subprocess.Popen([
        CHROME_EXE,
        f"--profile-directory={profile_name}",
        f"--remote-debugging-port={DEBUG_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    await asyncio.sleep(4)  # 等Chrome启动

    sso_token = None
    domain = ""

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}", timeout=10000)
            context = browser.contexts[0]

            # 创建新页面访问 grok.com
            page = await context.new_page()
            try:
                await page.goto("https://grok.com", wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass  # 可能超时但cookies已加载

            await asyncio.sleep(2)

            # 提取 cookies (CDP 可以看到 httpOnly cookies)
            cookies = await context.cookies(["https://grok.com", "https://x.com"])
            for cookie in cookies:
                if cookie["name"] == "sso" and cookie.get("value"):
                    sso_token = cookie["value"]
                    domain = cookie.get("domain", "")
                    print(f"  [+] SSO found! domain={domain} len={len(sso_token)}")
                    print(f"      {sso_token[:30]}...{sso_token[-15:]}")
                    break

            if not sso_token:
                grok_cookies = [c["name"] for c in cookies if "grok" in c.get("domain","") or "x.com" in c.get("domain","")]
                print(f"  [-] No SSO. Grok/X cookies: {grok_cookies[:10]}")

            await browser.close()

        except Exception as e:
            print(f"  [-] CDP Error: {e}")

    # 关闭 Chrome
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except:
        proc.kill()

    await asyncio.sleep(2)  # 等端口释放

    if sso_token:
        return {"token": sso_token, "domain": domain, "profile": profile_name}
    return None


async def main():
    print("=" * 60)
    print("Chrome CDP SSO 提取")
    print("=" * 60)

    # 确保没有 Chrome 残留
    subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)
    await asyncio.sleep(2)

    tokens = []

    for profile in PROFILES:
        print(f"\n--- {profile} ---")
        result = await extract_from_profile(profile)
        if result and result["token"] not in [t["token"] for t in tokens]:
            tokens.append(result)

    # 确保 Chrome 全部关闭
    subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)

    print(f"\n{'='*60}")
    print(f"提取到 {len(tokens)} 个唯一 SSO Token")

    # Save
    output = os.path.join(SCRIPT_DIR, "data", "extracted_tokens.json")
    with open(output, "w") as f:
        json.dump(tokens, f, indent=2)
    print(f"已保存: {output}")

    # Import to grok2api
    if tokens:
        print(f"\n导入到 grok2api ({GROK2API_URL})...")
        try:
            tlist = [{"token": t["token"], "status": "active", "quota": 80, "tags": [], "note": f"auto-{t['profile']}"} for t in tokens]
            data = json.dumps({"ssoBasic": tlist}).encode()
            req = urllib.request.Request(
                f"{GROK2API_URL}/v1/admin/tokens",
                data=data,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {ADMIN_PWD}"},
                method="POST"
            )
            resp = urllib.request.urlopen(req, timeout=10)
            print(f"  API: {resp.status} {resp.read().decode()[:200]}")
        except Exception as e:
            print(f"  API错误: {e}")

    if tokens:
        print(f"\n{'='*60}")
        print("Token 列表:")
        for i, t in enumerate(tokens, 1):
            print(f"#{i} [{t['profile']}] [{t['domain']}]: {t['token'][:40]}...")

    return tokens


if __name__ == "__main__":
    asyncio.run(main())
