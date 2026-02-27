"""End-to-end test: generate image then use it to generate video."""
import urllib.request
import json
import time

API_BASE = "https://grok2api.fly.dev"
KEY = "zimagenart@worldno1"

def test_e2e():
    # Step 1: Generate an image
    print("=== Step 1: Generating image ===")
    data = json.dumps({
        "prompt": "beautiful woman in a garden", 
        "n": 1, 
        "size": "1024x1024"
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
    
    print(f"Image response: {json.dumps(img_res, indent=2)[:500]}")
    
    image_url = None
    if "data" in img_res and img_res["data"]:
        image_url = img_res["data"][0].get("url", "")
    
    if not image_url:
        print("ERROR: No image URL returned!")
        return
    
    print(f"\nImage URL: {image_url}")
    
    # Step 2: Use image to generate video
    print("\n=== Step 2: Starting video with image ===")
    vid_data = json.dumps({
        "prompt": "the woman starts walking gracefully",
        "resolution_name": "480p",
        "video_length": 6,
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
        print("ERROR: No task_id!")
        return
    
    # Step 3: Subscribe to SSE
    print(f"\n=== Step 3: SSE stream for {task_id} ===")
    sse_url = f"{API_BASE}/v1/public/video/sse?task_id={task_id}"
    sse_req = urllib.request.Request(sse_url, headers={"Authorization": f"Bearer {KEY}"})
    
    with urllib.request.urlopen(sse_req, timeout=300) as sse:
        for line in sse:
            decoded = line.decode('utf-8').strip()
            if decoded:
                print(f">>> {decoded}")
                if "video" in decoded.lower() and ("mp4" in decoded.lower() or "generated_video" in decoded.lower()):
                    print("\n*** VIDEO URL DETECTED! ***")

if __name__ == "__main__":
    test_e2e()
