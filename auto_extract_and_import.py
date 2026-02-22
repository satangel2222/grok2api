"""
全自动: 关闭Chrome → 提取SSO → 导入grok2api → 重开Chrome
需要管理员权限运行 (Chrome v130+ app-bound encryption)
"""
import rookiepy
import json
import os
import sys
import subprocess
import time

GROK2API_URL = "http://localhost:8001"
ADMIN_PASSWORD = "grok2api"

def extract_all_tokens():
    """从所有Chrome配置文件提取SSO token"""
    tokens = []
    profiles = ["Default", "Profile 1", "Profile 2", "Profile 3"]

    for profile in profiles:
        try:
            cookies = rookiepy.chrome(
                domains=[".grok.com", ".x.com", "grok.com", "x.com"],
                profiles=[profile]
            )
            for c in cookies:
                if c.get("name") == "sso" and c.get("value"):
                    token = c["value"]
                    domain = c.get("domain", "unknown")
                    if token not in [t["token"] for t in tokens]:
                        tokens.append({
                            "token": token,
                            "domain": domain,
                            "profile": profile
                        })
                        print(f"  [+] {profile} | {domain} | {token[:25]}...{token[-10:]}")
        except Exception as e:
            err = str(e)
            if "appbound" in err.lower() or "admin" in err.lower():
                print(f"  [!] {profile}: 需要管理员权限 - {err[:80]}")
            else:
                print(f"  [-] {profile}: {err[:80]}")

    return tokens

def import_to_grok2api(tokens, pool="ssoBasic"):
    """通过API导入token到grok2api"""
    import urllib.request
    import urllib.error

    # 构建请求体
    token_list = []
    for t in tokens:
        token_list.append({
            "token": t["token"],
            "status": "active",
            "quota": 80 if pool == "ssoBasic" else 140,
            "tags": [],
            "note": f"auto-{t['profile']}"
        })

    payload = {pool: token_list}
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        f"{GROK2API_URL}/v1/admin/tokens",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ADMIN_PASSWORD}"
        },
        method="POST"
    )

    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        print(f"  API响应: {resp.status} - {json.dumps(result, ensure_ascii=False)[:200]}")
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  API错误: {e.code} - {body[:200]}")
        return False
    except Exception as e:
        print(f"  连接错误: {e}")
        return False

def main():
    print("=" * 60)
    print("Grok SSO Token 全自动提取 & 导入")
    print("=" * 60)

    # Step 1: 提取 tokens
    print("\n[Step 1] 提取 SSO Tokens...")
    tokens = extract_all_tokens()

    if not tokens:
        print("\n❌ 未找到任何 SSO Token!")
        print("可能原因: Chrome中未登录grok.com, 或cookie已过期")
        sys.exit(1)

    print(f"\n✅ 提取到 {len(tokens)} 个 Token")

    # 保存到文件
    output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "extracted_tokens.json")
    with open(output_file, "w") as f:
        json.dump(tokens, f, indent=2, ensure_ascii=False)
    print(f"已保存: {output_file}")

    # Step 2: 导入到 grok2api
    print(f"\n[Step 2] 导入到 grok2api ({GROK2API_URL})...")
    success = import_to_grok2api(tokens)

    if success:
        print("\n✅ 全部导入成功!")
    else:
        print("\n⚠️ API导入失败, 但token已保存到文件, 可手动导入")

    # Step 3: 验证
    print(f"\n[Step 3] 验证token状态...")
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{GROK2API_URL}/v1/admin/tokens",
            headers={"Authorization": f"Bearer {ADMIN_PASSWORD}"}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())

        total = 0
        for pool_name, pool_tokens in data.items():
            if isinstance(pool_tokens, list):
                total += len(pool_tokens)
                print(f"  {pool_name}: {len(pool_tokens)} tokens")
        print(f"  总计: {total} tokens")
    except Exception as e:
        print(f"  验证失败: {e}")

    # 输出纯token列表
    print("\n" + "=" * 60)
    print("纯 Token 列表:")
    print("=" * 60)
    for i, t in enumerate(tokens, 1):
        print(f"\n#{i} [{t['profile']}] [{t['domain']}]")
        print(t['token'])

if __name__ == "__main__":
    main()
