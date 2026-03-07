"""
Video extend (continue/chain) service.

Extends an existing Grok video by 6 seconds using the same account
that generated the original. Uses video_token_cache for token lookup.

Dependencies:
  - video_token_cache (standalone module, no video.py imports)
  - AppChatReverse (existing reverse API)
  - ResettableSession (existing session helper)
"""

import re
import asyncio
from typing import Optional

from app.core.logger import logger
from app.core.config import get_config
from app.core.exceptions import AppException, UpstreamException, ValidationException
from app.services.grok.services.video_token_cache import get_video_token, store_video_token
from app.services.reverse.app_chat import AppChatReverse
from app.services.reverse.utils.session import ResettableSession


def _new_session() -> ResettableSession:
    """Create a fresh session for reverse API calls."""
    from app.services.grok.services.video import _new_session as _vs
    return _vs()


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
    Extend a video by generating a continuation from a parent post.

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

    # Resolve the token that originally generated this video
    token = token_override
    if not token:
        token = get_video_token(reference_id)
    if not token:
        raise AppException(
            message="Video extend failed: token not found for this video. "
                    "The original generation may have expired from cache (1hr TTL) "
                    "or was generated on a different instance.",
            status_code=404,
        )

    logger.info(
        f"Video extend: ref={reference_id}, prompt='{prompt[:50]}...', "
        f"token={token[:10]}..., ratio={aspect_ratio}, length={video_length}s"
    )

    # Build the message — extend uses the same prompt format
    message = (prompt or "Continue the video seamlessly").strip()

    # model_config_override tells Grok to extend from parent_post_id
    model_config_override = {
        "modelMap": {
            "videoGenModelConfig": {
                "aspectRatio": aspect_ratio,
                "parentPostId": reference_id,
                "resolutionName": resolution,
                "videoLength": video_length,
            }
        }
    }

    # Call the reverse API
    session = _new_session()
    try:
        async with asyncio.Semaphore(1):  # Single extend at a time per call
            stream_response = await AppChatReverse.request(
                session,
                token,
                message=message,
                model="grok-3",
                tool_overrides={"videoGen": True},
                model_config_override=model_config_override,
            )

            # Collect the response (extend is always non-streaming)
            import orjson
            video_url = ""
            thumbnail_url = ""
            new_video_post_id = ""

            async for raw_line in stream_response:
                if isinstance(raw_line, bytes):
                    raw_line = raw_line.decode("utf-8", errors="replace")
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = orjson.loads(line)
                except Exception:
                    continue

                resp = data.get("result", {}).get("response", {})
                if video_resp := resp.get("streamingVideoGenerationResponse"):
                    progress = video_resp.get("progress", 0)
                    if progress == 100:
                        video_url = video_resp.get("videoUrl", "")
                        thumbnail_url = video_resp.get("thumbnailImageUrl", "")
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
                            f"new_post_id={new_video_post_id!r}"
                        )
                        break

    except Exception as e:
        try:
            await session.close()
        except Exception:
            pass
        if isinstance(e, AppException):
            raise
        logger.error(f"Video extend error: {e}")
        raise UpstreamException(f"Video extend failed: {str(e)}")

    if not video_url:
        raise UpstreamException("Video extend failed: no video URL in response")

    # Store the NEW video's token for chain extending
    if new_video_post_id:
        store_video_token(new_video_post_id, token)
        logger.info(f"Stored token for chain extend: {new_video_post_id}")

    return {
        "video_url": video_url,
        "video_post_id": new_video_post_id,
        "thumbnail_url": thumbnail_url,
    }
