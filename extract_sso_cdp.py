"""
通过 Chrome DevTools Protocol 从运行中的 Chrome 提取 Grok SSO cookie
方案: 用 Playwright 启动新 Chrome 实例加载已有 profile，提取 cookie
"""
import asyncio
import json
import os
import sys
import subprocess
import time
import socket

# Chrome profiles to check
CHROME_USER_DATA = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
PROFILES = ["Default", "Profile 1", "Profile 2", "Profile 3"]
SSO_DOMAINS = ["grok.com", "x.com", "twitter.com"]


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


async def extract_via_cdp_per_profile(profile_name, port):
    """启动 Chrome 并通过 CDP 提取 cookies"""
    from playwright.async_api import async_playwright

    chrome_exe = None
    for path in [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]:
        if os.path.exists(path):
            chrome_exe = path
            break

    if not chrome_exe:
        print(f"  [-] Chrome executable not found")
        return []

    # 使用临时 user-data-dir 但复制 profile 的 cookie
    # 不行 — 加密绑定了原始路径

    # 方案: 直接通过 CDP 连接已运行的 Chrome
    # 需要 Chrome 启动时带 --remote-debugging-port
    # 当前 Chrome 没有这个参数, 所以我们需要另一个方法

    return []


async def extract_via_playwright_stealth():
    """
    使用 Playwright 的 chromium.launch 访问 grok.com
    用户需要在打开的浏览器中手动登录 (但我们可以自动化大部分)
    """
    from playwright.async_api import async_playwright

    tokens = []

    async with async_playwright() as p:
        # 使用持久化上下文 — 用一个临时目录
        for i, profile in enumerate(PROFILES):
            profile_dir = os.path.join(CHROME_USER_DATA, profile)
            if not os.path.exists(profile_dir):
                continue

            print(f"\n[Profile: {profile}]")

            try:
                # 尝试用 channel='chrome' 来用系统 Chrome
                # 用临时 user data dir 以避免锁冲突
                browser = await p.chromium.launch(
                    channel="chrome",
                    headless=False,
                    args=[
                        f"--profile-directory={profile}",
                    ]
                )

                context = await browser.new_context()
                page = await context.new_page()

                # 导航到 grok.com
                await page.goto("https://grok.com", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(3)

                # 提取 cookies
                cookies = await context.cookies(["https://grok.com", "https://x.com"])
                for cookie in cookies:
                    if cookie["name"] == "sso":
                        token = cookie["value"]
                        if token and token not in [t["token"] for t in tokens]:
                            tokens.append({
                                "token": token,
                                "domain": cookie["domain"],
                                "profile": profile,
                            })
                            print(f"  [+] Found SSO: {token[:20]}...{token[-10:]}")

                await browser.close()

            except Exception as e:
                print(f"  [-] Error: {e}")

    return tokens


def extract_via_console_script():
    """
    生成一个 JavaScript 片段，用户在 Chrome 控制台执行就能提取 SSO
    这是最可靠的方法
    """
    script = """
// === Grok SSO Token 一键提取 ===
// 在 grok.com 页面的 Chrome 控制台 (F12) 中运行此脚本

(function() {
    const cookies = document.cookie.split(';');
    let sso = null;
    for (const cookie of cookies) {
        const [name, ...valueParts] = cookie.trim().split('=');
        if (name === 'sso') {
            sso = valueParts.join('=');
            break;
        }
    }

    if (sso) {
        // 复制到剪贴板
        navigator.clipboard.writeText(sso).then(() => {
            console.log('%c✅ SSO Token 已复制到剪贴板!', 'color: green; font-size: 16px');
            console.log('Token 前20字符:', sso.substring(0, 20) + '...');
            alert('✅ SSO Token 已复制到剪贴板!\\n\\n前20字符: ' + sso.substring(0, 20) + '...');
        }).catch(() => {
            // fallback
            prompt('复制下面的 SSO Token:', sso);
        });
    } else {
        console.log('%c❌ 未找到 SSO cookie! 请确保已登录 grok.com', 'color: red; font-size: 16px');
        alert('❌ 未找到 SSO cookie!\\n请确保你已登录 grok.com');
    }
})();
""".strip()
    return script


def main():
    print("=" * 60)
    print("Grok SSO Token 批量提取")
    print("=" * 60)
    print(f"Chrome 配置文件: {PROFILES}")
    print()

    # 方案 1: 先尝试管理员模式的 rookiepy
    print("[尝试方案1] rookiepy + 管理员权限...")
    try:
        # 创建一个提升权限的子脚本
        admin_script = os.path.join(os.path.dirname(__file__), "_admin_extract.py")
        with open(admin_script, "w") as f:
            f.write("""
import rookiepy
import json
import os

tokens = []
profiles = ["Default", "Profile 1", "Profile 2", "Profile 3"]

for profile in profiles:
    try:
        cookies = rookiepy.chrome(domains=[".grok.com", ".x.com"], profiles=[profile])
        for c in cookies:
            if c.get("name") == "sso" and c.get("value"):
                token = c["value"]
                if token not in [t["token"] for t in tokens]:
                    tokens.append({"token": token, "domain": c.get("domain",""), "profile": profile})
                    print(f"[+] {profile}: {token[:20]}...")
    except Exception as e:
        print(f"[-] {profile}: {e}")

output = os.path.join(os.path.dirname(__file__), "data", "extracted_tokens.json")
with open(output, "w") as f:
    json.dump(tokens, f, indent=2)
print(f"\\nSaved {len(tokens)} tokens to {output}")
""")

        # 尝试以管理员运行
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f'Start-Process python -ArgumentList "{admin_script}" -Verb RunAs -Wait -WindowStyle Normal'],
            capture_output=True, text=True, timeout=30
        )

        # 检查结果
        output_file = os.path.join(os.path.dirname(__file__), "data", "extracted_tokens.json")
        if os.path.exists(output_file):
            with open(output_file) as f:
                tokens = json.load(f)
            if tokens:
                print(f"\n✅ 成功提取 {len(tokens)} 个 token!")
                for i, t in enumerate(tokens, 1):
                    print(f"  Token {i} [{t['profile']}]: {t['token'][:30]}...")
                return tokens
    except Exception as e:
        print(f"  方案1 失败: {e}")

    # 方案 2: JavaScript 控制台方案 (最可靠的备选)
    print("\n[方案2] 生成 JavaScript 提取脚本...")
    script = extract_via_console_script()

    script_file = os.path.join(os.path.dirname(__file__), "data", "extract_sso.js")
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script)

    print(f"\n✅ JavaScript 提取脚本已保存到: {script_file}")
    print("\n" + "=" * 60)
    print("操作步骤 (每个账号 ~10秒):")
    print("=" * 60)
    print("1. 在 Chrome 中打开 grok.com 并登录")
    print("2. 按 F12 打开开发者工具")
    print("3. 切换到 Console 标签")
    print("4. 粘贴以下脚本并回车:")
    print("-" * 40)
    print(script)
    print("-" * 40)
    print("\n5. Token 会自动复制到剪贴板")
    print("6. 粘贴到记事本, 换下一个账号重复")

    return []


if __name__ == "__main__":
    tokens = main()

    if tokens:
        print("\n" + "=" * 60)
        print("准备导入到 grok2api...")
        print("=" * 60)

        # 纯 token 列表
        print("\n纯 Token 列表:")
        for i, t in enumerate(tokens, 1):
            print(f"{i}. {t['token']}")
