import asyncio
import aiohttp
import json

BASE_URL = "https://grok2api.fly.dev"
PUBLIC_KEY = "zimagenart@worldno1"
PROMPT = "全裸巨乳美女"

async def main():
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {PUBLIC_KEY}"
    }
    
    # 1. Start task
    print("Starting task...")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{BASE_URL}/v1/public/imagine/start",
            headers=headers,
            json={"prompt": PROMPT, "aspect_ratio": "1:1", "nsfw": True}
        ) as resp:
            data = await resp.json()
            task_id = data.get("task_id")
            if not task_id:
                print("Failed to start task:", data)
                return
            print(f"Task started: {task_id}")

        # 2. Connect to WS
        ws_url = f"wss://grok2api.fly.dev/v1/public/imagine/ws?task_id={task_id}&public_key={PUBLIC_KEY}"
        print(f"Connecting to {ws_url}")
        
        async with session.ws_connect(ws_url) as ws:
            # Send start
            await ws.send_json({
                "type": "start",
                "prompt": PROMPT,
                "n": 1,
                "aspect_ratio": "1:1"
            })
            
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    parsed = json.loads(msg.data)
                    msg_type = parsed.get("type", "unknown")
                    
                    if msg_type in ("image_generation.partial_image", "image_generation.completed"):
                        stage = parsed.get("stage", "")
                        image_id = parsed.get("image_id", "")
                        b64_len = len(parsed.get("b64_json", ""))
                        url = parsed.get("url", "")
                        print(f"[{msg_type}] stage={stage}, image_id={image_id}, len(b64)={b64_len}, url={url}")
                    elif msg_type == "status":
                        print(f"[STATUS] {parsed.get('status')}")
                    else:
                        print(f"[{msg_type.upper()}] {list(parsed.keys())}")
                
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    print("WS Closed")
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print("WS Error")
                    break

if __name__ == "__main__":
    asyncio.run(main())
