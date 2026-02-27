import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.services.reverse.ws_imagine import ImagineWebSocketReverse
from app.services.token.manager import get_token_manager
from app.core.config import get_config

async def main():
    token_mgr = await get_token_manager()
    await token_mgr.reload_if_stale()
    if not token_mgr.pools:
        print("No pools")
        return
    pool_name = list(token_mgr.pools.keys())[0]
    token = token_mgr.get_token(pool_name)
    if not token:
        print("No token available")
        return

    print("Using token:", token[:10])
    service = ImagineWebSocketReverse()
    
    stream = service.stream(
        token=token,
        prompt="全裸巨乳美女",
        aspect_ratio="16:9",
        n=4,
        enable_nsfw=True
    )
    
    async for item in stream:
        if item.get("type") == "image":
            print(f"IMAGE: id={item.get('image_id')}, stage={item.get('stage')}, final={item.get('is_final')}, blob_size={item.get('blob_size')}, url={item.get('url')}")
        else:
            print(f"OTHER: {item}")

if __name__ == "__main__":
    asyncio.run(main())
