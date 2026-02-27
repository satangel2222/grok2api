import urllib.request
import json

def test_video():
    url = "https://grok2api.fly.dev/v1/public/video/start"
    data = json.dumps({
        "prompt": "sexy lady walking in a park", 
        "resolution_name": "480p", 
        "video_length": 6,
        "image_url": "https://assets.grok.com/users/c5f8502f-b4df-441f-a590-b186b51c8db1/4v59gUq0Boz6hU0y_image.jpeg"
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Authorization": "Bearer zimagenart@worldno1"})
    
    print("Starting video...")
    with urllib.request.urlopen(req) as r:
        start_res = json.loads(r.read().decode())
        print(f"Start Response: {start_res}")
        
        task_id = start_res.get("task_id")
        if not task_id:
            return
            
        print(f"Subscribing to SSE for {task_id}...")
        sse_url = f"https://grok2api.fly.dev/v1/public/video/sse?task_id={task_id}"
        sse_req = urllib.request.Request(sse_url, headers={"Authorization": "Bearer zimagenart@worldno1"})
        
        with urllib.request.urlopen(sse_req) as sse_resp:
            for line in sse_resp:
                decoded = line.decode('utf-8').strip()
                if decoded:
                    print(">>> CHUNK:", decoded)

if __name__ == "__main__":
    test_video()

