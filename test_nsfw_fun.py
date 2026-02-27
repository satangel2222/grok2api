"""Test NSFW video with FUN preset (extremely-crazy mode) like grok.868986.xyz."""
import urllib.request
import json

API_BASE = "https://grok2api.fly.dev"
KEY = "zimagenart@worldno1"

def test():
    # Step 1: Generate an NSFW image
    print("=== Step 1: Generating NSFW image ===")
    data = json.dumps({
        "prompt": "全裸巨乳美女", 
        "n": 1, 
        "size": "720x1280"
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/v1/images/generations",
        data=data,
        headers={
            "Content-Type": "application/json", 
            "Authorization": f"Bearer {KEY}"
        }
    )
    
    with urllib.request.urlopen(req, timeout=120) as r:
        img_res = json.loads(r.read().decode())
    
    image_url = img_res.get("data", [{}])[0].get("url", "")
    print(f"Image URL: {image_url}")
    
    if not image_url:
        print("No image!")
        return
    
    # Step 2: Start video with FUN preset (like grok.868986.xyz!)
    print("\n=== Step 2: Starting NSFW video with FUN preset ===")
    vid_data = json.dumps({
        "prompt": "she slowly moves her body",
        "resolution_name": "480p",
        "video_length": 6,
        "preset": "fun",  # <-- KEY: use 'fun' not 'spicy'!
        "image_url": image_url
    }).encode("utf-8")
    vid_req = urllib.request.Request(
        f"{API_BASE}/v1/public/video/start",
        data=vid_data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {KEY}"
        }
    )
    
    with urllib.request.urlopen(vid_req, timeout=30) as r:
        start_res = json.loads(r.read().decode())
    
    print(f"Video start: {start_res}")
    task_id = start_res.get("task_id")
    if not task_id:
        print("No task_id!")
        return
    
    # Step 3: Full SSE stream
    print(f"\n=== Step 3: SSE stream ===")
    sse_url = f"{API_BASE}/v1/public/video/sse?task_id={task_id}"
    sse_req = urllib.request.Request(sse_url, headers={"Authorization": f"Bearer {KEY}"})
    
    full_text = ""
    with urllib.request.urlopen(sse_req, timeout=300) as sse:
        for line in sse:
            decoded = line.decode('utf-8').strip()
            if not decoded:
                continue
            print(f">>> {decoded[:200]}")
            
            if decoded.startswith("data: ") and decoded != "data: [DONE]":
                try:
                    payload = json.loads(decoded[6:])
                    for choice in payload.get("choices", []):
                        content = choice.get("delta", {}).get("content", "")
                        if content:
                            full_text += content
                except:
                    pass
    
    print(f"\n=== FULL TEXT RESPONSE ===")
    print(full_text[:500])
    
    # Check for video URL
    if "grok2api" in full_text or "mp4" in full_text.lower() or "video" in full_text.lower():
        print("\n*** POSSIBLE VIDEO URL IN RESPONSE! ***")

if __name__ == "__main__":
    test()
