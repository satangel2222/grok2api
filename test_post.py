import urllib.request
import json

def main():
    print("Sending request...")
    req = urllib.request.Request(
        "http://127.0.0.1:8001/v1/images/generations",
        data=json.dumps({"prompt": "全裸巨乳美女", "n": 4, "size": "1024x1024"}).encode("utf-8"),
        headers={"Authorization": "Bearer zimagenart@worldno1", "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"Status: {resp.status}")
            print(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
