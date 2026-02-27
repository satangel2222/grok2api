"""
Admin endpoint: Test SSO token for NSFW video moderation.
"""

import orjson
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from curl_cffi.requests import AsyncSession

from app.core.config import get_config
from app.core.logger import logger
from app.core.auth import verify_app_key
from app.services.reverse.app_chat import AppChatReverse

router = APIRouter()


class ModerationTestRequest(BaseModel):
    token: str
    prompt: str = "a beautiful woman slowly dancing --mode=extremely-crazy"


class ModerationTestResponse(BaseModel):
    token_short: str
    moderated: Optional[bool] = None
    mode: Optional[str] = None
    video_url: Optional[str] = None
    error: Optional[str] = None


@router.post(
    "/test-moderation",
    response_model=ModerationTestResponse,
    dependencies=[Depends(verify_app_key)],
)
async def test_moderation(data: ModerationTestRequest):
    """Test if a token's NSFW video generation is moderated."""
    token = data.token.strip()
    if token.startswith("sso="):
        token = token[4:]

    result = ModerationTestResponse(token_short=f"...{token[-20:]}")

    try:
        session = AsyncSession()
        try:
            tool_overrides = {"videoGen": True}
            model_config_override = {
                "modelMap": {
                    "videoGenModelConfig": {
                        "aspectRatio": "16:9",
                        "resolutionName": "480p",
                        "videoLength": 6,
                        "mode": "extremely-crazy",
                    }
                }
            }

            response = await AppChatReverse.request(
                session,
                token,
                data.prompt,
                "grok-3",
                mode=None,
                file_attachments=None,
                tool_overrides=tool_overrides,
                model_config_override=model_config_override,
            )

            async for line in response:
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = orjson.loads(line)
                except Exception:
                    continue

                r = obj.get("result", {}).get("response", {})

                # Check error
                err_type = r.get("errorType")
                if err_type:
                    result.error = err_type
                    logger.info(f"Moderation test: error={err_type} token=...{token[-12:]}")
                    return result

                # Check video response
                vr = r.get("streamingVideoGenerationResponse")
                if vr and vr.get("progress") == 100:
                    result.moderated = vr.get("moderated")
                    result.mode = vr.get("mode")
                    result.video_url = vr.get("videoUrl") or None
                    logger.info(
                        f"Moderation test: moderated={result.moderated} "
                        f"mode={result.mode} url={bool(result.video_url)} "
                        f"token=...{token[-12:]}"
                    )
                    return result

            result.error = "no_video_100_progress"

        finally:
            await session.close()

    except Exception as e:
        result.error = str(e)[:200]
        logger.warning(f"Moderation test failed: {e}")

    return result


__all__ = ["router"]
