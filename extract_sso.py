"""
从 Chrome 所有配置文件中提取 Grok SSO cookie
"""
import rookiepy
import os
import json
import sqlite3
import shutil
import tempfile
import sys

CHROME_USER_DATA = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")

# Grok SSO cookie 可能存在的域名
SSO_DOMAINS = [".grok.com", "grok.com", ".x.com", "x.com", ".twitter.com", "twitter.com"]

def get_chrome_profiles():
    """列出所有 Chrome 配置文件"""
    profiles = []
    if not os.path.exists(CHROME_USER_DATA):
        print(f"Chrome User Data not found: {CHROME_USER_DATA}")
        return profiles

    for item in os.listdir(CHROME_USER_DATA):
        full_path = os.path.join(CHROME_USER_DATA, item)
        if os.path.isdir(full_path):
            if item == "Default" or item.startswith("Profile "):
                # Check if Cookies file exists
                cookies_path = os.path.join(full_path, "Cookies")
                network_cookies = os.path.join(full_path, "Network", "Cookies")
                if os.path.exists(cookies_path) or os.path.exists(network_cookies):
                    profiles.append(item)
    return profiles


def extract_with_rookiepy():
    """使用 rookiepy 提取所有 Chrome cookie 中的 SSO"""
    print("=" * 60)
    print("方法1: rookiepy 提取 (需要 Chrome 关闭)")
    print("=" * 60)

    found_tokens = []

    try:
        # rookiepy 默认读取 Default 配置
        cookies = rookiepy.chrome(domains=[".grok.com", ".x.com"])
        for cookie in cookies:
            if cookie.get("name") == "sso":
                token = cookie.get("value", "")
                domain = cookie.get("domain", "unknown")
                if token and token not in [t["token"] for t in found_tokens]:
                    found_tokens.append({
                        "token": token,
                        "domain": domain,
                        "profile": "Default",
                        "source": "rookiepy"
                    })
                    print(f"  [+] Found SSO token from {domain} (Default profile)")
                    print(f"      Token: {token[:20]}...{token[-10:]}")
    except Exception as e:
        print(f"  [-] rookiepy Default failed: {e}")

    # Try each profile
    profiles = get_chrome_profiles()
    for profile in profiles:
        if profile == "Default":
            continue
        try:
            profile_path = os.path.join(CHROME_USER_DATA, profile)
            cookies = rookiepy.chrome(domains=[".grok.com", ".x.com"], profile=profile)
            for cookie in cookies:
                if cookie.get("name") == "sso":
                    token = cookie.get("value", "")
                    domain = cookie.get("domain", "unknown")
                    if token and token not in [t["token"] for t in found_tokens]:
                        found_tokens.append({
                            "token": token,
                            "domain": domain,
                            "profile": profile,
                            "source": "rookiepy"
                        })
                        print(f"  [+] Found SSO token from {domain} ({profile})")
                        print(f"      Token: {token[:20]}...{token[-10:]}")
        except Exception as e:
            print(f"  [-] rookiepy {profile} failed: {e}")

    return found_tokens


def extract_direct_sqlite():
    """直接读取 Chrome SQLite cookie 数据库 (备选方案)"""
    print("\n" + "=" * 60)
    print("方法2: 直接 SQLite 读取 (可在 Chrome 运行时使用)")
    print("=" * 60)

    found_tokens = []
    profiles = get_chrome_profiles()

    for profile in profiles:
        profile_path = os.path.join(CHROME_USER_DATA, profile)

        # Chrome 新版本在 Network 子目录
        cookies_paths = [
            os.path.join(profile_path, "Network", "Cookies"),
            os.path.join(profile_path, "Cookies"),
        ]

        for cookies_path in cookies_paths:
            if not os.path.exists(cookies_path):
                continue

            try:
                # 复制数据库避免锁定问题
                tmp = tempfile.mktemp(suffix=".db")
                shutil.copy2(cookies_path, tmp)

                conn = sqlite3.connect(tmp)
                cursor = conn.cursor()

                # 查询 SSO cookie
                for domain in SSO_DOMAINS:
                    cursor.execute(
                        "SELECT host_key, name, value, encrypted_value, path "
                        "FROM cookies WHERE host_key LIKE ? AND name = 'sso'",
                        (f"%{domain}%",)
                    )
                    rows = cursor.fetchall()
                    for row in rows:
                        host, name, value, enc_value, path = row
                        # value 可能为空(加密了), encrypted_value 才有值
                        if value:
                            token = value
                        else:
                            token = f"[ENCRYPTED - length {len(enc_value)} bytes]"

                        print(f"  [*] {profile} | {host} | name={name} | value={'YES' if value else 'ENCRYPTED'} | enc_len={len(enc_value)}")

                        if value and value not in [t["token"] for t in found_tokens]:
                            found_tokens.append({
                                "token": value,
                                "domain": host,
                                "profile": profile,
                                "source": "sqlite"
                            })

                conn.close()
                os.unlink(tmp)

            except Exception as e:
                print(f"  [-] SQLite {profile} ({cookies_path}): {e}")
                try:
                    os.unlink(tmp)
                except:
                    pass

    return found_tokens


def main():
    print("Grok SSO Cookie 批量提取工具")
    print(f"Chrome 数据目录: {CHROME_USER_DATA}")

    profiles = get_chrome_profiles()
    print(f"发现 {len(profiles)} 个 Chrome 配置文件: {profiles}")
    print()

    all_tokens = []

    # 方法1: rookiepy
    tokens1 = extract_with_rookiepy()
    all_tokens.extend(tokens1)

    # 方法2: 直接 SQLite (备选)
    tokens2 = extract_direct_sqlite()
    # 去重合并
    existing = {t["token"] for t in all_tokens}
    for t in tokens2:
        if t["token"] not in existing and not t["token"].startswith("[ENCRYPTED"):
            all_tokens.append(t)

    # 结果
    print("\n" + "=" * 60)
    print(f"总计提取到 {len(all_tokens)} 个唯一 SSO Token")
    print("=" * 60)

    if all_tokens:
        # 保存到文件
        output_file = os.path.join(os.path.dirname(__file__), "data", "extracted_tokens.json")
        with open(output_file, "w") as f:
            json.dump(all_tokens, f, indent=2)
        print(f"\n已保存到: {output_file}")

        # 打印纯 token 列表
        print("\n--- 纯 Token 列表 (可直接导入 grok2api) ---")
        for i, t in enumerate(all_tokens, 1):
            print(f"Token {i} [{t['profile']}] [{t['domain']}]:")
            print(f"  {t['token'][:40]}...{t['token'][-20:]}")
            print()
    else:
        print("\n未找到任何 SSO Token!")
        print("可能原因:")
        print("  1. Chrome 中未登录 grok.com")
        print("  2. Cookie 已过期")
        print("  3. Chrome 正在运行且数据库被锁定")
        print("  4. 7个账号可能不在不同的 Chrome Profile 里")
        print("\n建议: 确保至少在一个 Chrome 窗口中登录了 grok.com")


if __name__ == "__main__":
    main()
