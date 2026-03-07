"""
Video extend (continue/chain) service.

Extends an existing Grok video by 6 seconds using the same account
AND the same conversation that generated the original.

Key insight: Grok video extend requires staying in the SAME conversation
(conversationId). Using /conversations/new creates unrelated content.

Dependencies:
  - video_token_cache (standalone module, no video.py imports)
  - Grok REST API (direct HTTP, not via AppChatReverse)
  - ResettableSession (existing session helper)
"""

import re
import uuid
from typing import Optional

import orjson

from app.core.logger import logger
from app.core.config import get_config
from app.core.exceptions import AppException, UpstreamException, ValidationException
from app.services.grok.services.video_token_cache import (
    get_video_context, store_video_context,
)
from app.services.reverse.utils.headers import build_headers
from app.services.reverse.utils.session import ResettableSession


# Conversation continuation endpoint (NOT /new)
CHAT_CONTINUE_API = "https://grok.com/rest/app-chat/conversations/{conversation_id}/responses"


def _new_session() -> ResettableSession:
    """Create a fresh session for reverse API calls."""
    browser = get_config("proxy.browser")
    if browser:
        return ResettableSession(impersonate=browser)
    return ResettableSession()


def _build_extend_payload(
    message: str,
    model: str,
    aspect_ratio: str,
    parent_post_id: str,
    resolution: str,
    video_length: int,
) -> dict:
    """Build payload for video extend (conversation continuation)."""
    return {
        "deviceEnvInfo": {
            "darkModeEnabled": False,
            "devicePixelRatio": 2,
            "screenWidth": 2056,
            "screenHeight": 1329,
            "viewportWidth": 2056,
            "viewportHeight": 1083,
        },
        "disableMemory": get_config("app.disable_memory"),
        "disableSearch": False,
        "disableSelfHarmShortCircuit": True,
        "disableTextFollowUps": False,
        "enableImageGeneration": True,
        "enableImageStreaming": True,
        "enableNsfw": True,
        "enableSideBySide": True,
        "fileAttachments": [],
        "forceConcise": False,
        "forceSideBySide": False,
        "imageAttachments": [],
        "imageGenerationCount": 2,
        "isAsyncChat": False,
        "isReasoning": False,
        "message": message,
        "modelName": model,
        "responseMetadata": {
            "requestModelDetails": {"modelId": model},
            "modelConfigOverride": {
                "modelMap": {
                    "videoGenModelConfig": {
                        "aspectRatio": aspect_ratio,
                        "mode": "extremely-spicy-or-crazy",
                        "parentPostId": parent_post_id,
                        "resolutionName": resolution,
                        "videoLength": video_length,
                    }
                }
            },
        },
        "returnImageBytes": False,
        "returnRawGrokInXaiRequest": False,
        "sendFinalMetadata": True,
        "temporary": get_config("app.temporary"),
        "toolOverrides": {"videoGen": True},
    }


def _extract_video_id(video_url: str) -> str:
    """Extract video post ID from a Grok video URL."""
    if not video_url:
        return ""
    match = re.search(r"/generated/([0-9a-fA-F-]{32,36})/", video_url)
    if match:
        return match.group(1)
    match = re.search(r"/([0-9a-fA-F-]{32,36})/generated_video", video_url)
    if match:
        return match.group(1)
    return ""


async def extend_video(
    reference_id: str,
    prompt: str = "",
    aspect_ratio: str = "3:2",
    video_length: int = 6,
    resolution: str = "480p",
    token_override: Optional[str] = None,
) -> dict:
    """
    Extend a video by generating a continuation in the SAME conversation.

    Args:
        reference_id: The video_post_id from the original generation
        prompt: Optional continuation prompt (empty = auto-continue)
        aspect_ratio: Must match original video
        video_length: Extension length in seconds (default 6)
        resolution: Must match original video
        token_override: Force a specific token (bypasses cache lookup)

    Returns:
        dict with keys: video_url, video_post_id, thumbnail_url
    """
    if not reference_id:
        raise ValidationException("reference_id (video_post_id) is required")

    # Resolve the context (token + conversation_id) from cache
    ctx = get_video_context(reference_id)

    token = token_override
    conversation_id = ""

    if ctx:
        if not token:
            token = ctx.token
        conversation_id = ctx.conversation_id

    if not token:
        raise AppException(
            message="Video extend failed: token not found for this video. "
                    "The original generation may have expired from cache (1hr TTL) "
                    "or was generated on a different instance.",
            status_code=404,
        )

    if not conversation_id:
        raise AppException(
            message="Video extend failed: conversation_id not found. "
                    "The original video must be generated first to capture the conversation context. "
                    "Cannot extend without the original conversation.",
            status_code=404,
        )

    logger.info(
        f"Video extend: ref={reference_id}, conv={conversation_id}, "
        f"prompt='{prompt[:50]}...', token_hash={hash(token)}, token_prefix={token[:30]}..., "
        f"ratio={aspect_ratio}, length={video_length}s"
    )

    # Build the continuation message (always append spicy mode flag)
    message = f"{(prompt or 'Continue the video seamlessly').strip()} --mode=extremely-spicy-or-crazy"

    # Build payload for conversation continuation
    payload = _build_extend_payload(
        message=message,
        model="grok-3",
        aspect_ratio=aspect_ratio,
        parent_post_id=reference_id,
        resolution=resolution,
        video_length=video_length,
    )

    # Build headers (same as AppChatReverse uses)
    headers = build_headers(
        cookie_token=token,
        content_type="application/json",
        origin="https://grok.com",
        referer="https://grok.com/",
    )

    # Build the continuation URL
    url = CHAT_CONTINUE_API.format(conversation_id=conversation_id)

    # Proxy config
    base_proxy = get_config("proxy.base_proxy_url")
    proxies = {"http": base_proxy, "https": base_proxy} if base_proxy else None
    timeout = max(
        float(get_config("chat.timeout") or 0),
        float(get_config("video.timeout") or 0),
        float(get_config("image.timeout") or 0),
    )
    browser = get_config("proxy.browser")

    session = _new_session()
    video_url = ""
    thumbnail_url = ""
    new_video_post_id = ""
    new_conversation_id = ""

    try:
        response = await session.post(
            url,
            headers=headers,
            data=orjson.dumps(payload),
            timeout=timeout,
            stream=True,
            proxies=proxies,
            impersonate=browser,
        )

        if response.status_code != 200:
            content = ""
            try:
                content = await response.text()
            except Exception:
                pass
            logger.error(
                f"Video extend HTTP {response.status_code}: {content[:500]}"
            )
            raise UpstreamException(
                message=f"Video extend failed: HTTP {response.status_code}",
                details={"status": response.status_code, "body": content},
            )

        # Parse streaming response
        async for raw_line in response.aiter_lines():
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="replace")
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = orjson.loads(line)
            except Exception:
                continue

            result = data.get("result", {})

            # Capture conversationId from response (for chain extend)
            if not new_conversation_id:
                conv = result.get("conversation", {})
                if conv_id := conv.get("conversationId"):
                    new_conversation_id = conv_id

            # Video response is directly under result (not nested in response)
            video_resp = result.get("streamingVideoGenerationResponse")
            if not video_resp:
                # Also check nested path (some endpoints use result.response.*)
                video_resp = result.get("response", {}).get("streamingVideoGenerationResponse")
            if video_resp:
                progress = video_resp.get("progress", 0)
                if progress < 100:
                    logger.debug(f"Video extend progress: {progress}%")
                    continue

                # progress == 100 — video is ready
                raw_video_url = video_resp.get("videoUrl", "")
                raw_thumb_url = video_resp.get("thumbnailImageUrl", "")

                # Grok may return relative paths — prepend base URL
                base = "https://assets.grok.com/"
                video_url = raw_video_url if raw_video_url.startswith("http") else (base + raw_video_url) if raw_video_url else ""
                thumbnail_url = raw_thumb_url if raw_thumb_url.startswith("http") else (base + raw_thumb_url) if raw_thumb_url else ""
                raw_post_id = video_resp.get("videoPostId", "")
                raw_parent_id = video_resp.get("parentPostId", "")
                new_video_post_id = raw_post_id or raw_parent_id

                # Fallback: construct URL from IDs
                if not video_url and (raw_post_id or raw_parent_id):
                    image_ref = video_resp.get("imageReference", "")
                    vid = raw_post_id or raw_parent_id
                    m = re.match(
                        r"https://assets\.grok\.com/users/([^/]+)/",
                        image_ref,
                    )
                    if m:
                        video_url = (
                            f"https://assets.grok.com/users/{m.group(1)}"
                            f"/generated/{vid}/generated_video.mp4"
                        )

                logger.info(
                    f"Video extend complete: url={video_url!r}, "
                    f"new_post_id={new_video_post_id!r}, "
                    f"conv={new_conversation_id!r}"
                )
                break

    except Exception as e:
        try:
            await session.close()
        except Exception:
            pass
        if isinstance(e, (AppException, UpstreamException)):
            raise
        logger.error(f"Video extend error: {e}")
        raise UpstreamException(f"Video extend failed: {str(e)}")
    finally:
        try:
            await session.close()
        except Exception:
            pass

    if not video_url:
        raise UpstreamException("Video extend failed: no video URL in response")

    # Store the NEW video's context for chain extending
    # Use the same conversation_id so next extend stays in the same conversation
    chain_conv = new_conversation_id or conversation_id
    if new_video_post_id:
        store_video_context(new_video_post_id, token, chain_conv)
        logger.info(
            f"Stored context for chain extend: post={new_video_post_id}, conv={chain_conv}"
        )

    # Proxy the video through grok2api's download service
    from app.services.grok.utils.download import DownloadService
    dl = DownloadService()
    try:
        final_video_url = await dl.resolve_url(video_url, token, "video")
        final_thumb_url = ""
        if thumbnail_url:
            final_thumb_url = await dl.resolve_url(thumbnail_url, token, "image")
    except Exception as e:
        logger.warning(f"Video extend: failed to proxy URLs: {e}")
        final_video_url = video_url
        final_thumb_url = thumbnail_url

    return {
        "video_url": final_video_url,
        "video_post_id": new_video_post_id,
        "thumbnail_url": final_thumb_url,
    }
