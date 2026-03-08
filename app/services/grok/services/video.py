"""
Grok video generation service.
"""

import asyncio
import uuid
import re
from typing import Any, AsyncGenerator, AsyncIterable, Optional

import orjson
from curl_cffi.requests.errors import RequestsError

from app.core.logger import logger
from app.core.config import get_config
from app.core.exceptions import (
    UpstreamException,
    AppException,
    ValidationException,
    ErrorType,
    StreamIdleTimeoutError,
)
from app.services.grok.services.model import ModelService
from app.services.token import get_token_manager, EffortType
from app.services.grok.utils.stream import wrap_stream_with_usage
from app.services.grok.utils.process import (
    BaseProcessor,
    _with_idle_timeout,
    _normalize_line,
    _is_http2_error,
)
from app.services.grok.utils.retry import rate_limited
from app.services.reverse.app_chat import AppChatReverse
from app.services.reverse.media_post import MediaPostReverse
from app.services.reverse.video_upscale import VideoUpscaleReverse
from app.services.reverse.utils.session import ResettableSession
from app.services.token.manager import BASIC_POOL_NAME
from app.services.grok.services.video_token_cache import store_video_context

_VIDEO_SEMAPHORE = None
_VIDEO_SEM_VALUE = 0

def _get_video_semaphore() -> asyncio.Semaphore:
    """Reverse 接口并发控制（video 服务）。"""
    global _VIDEO_SEMAPHORE, _VIDEO_SEM_VALUE
    value = max(1, int(get_config("video.concurrent")))
    if value != _VIDEO_SEM_VALUE:
        _VIDEO_SEM_VALUE = value
        _VIDEO_SEMAPHORE = asyncio.Semaphore(value)
    return _VIDEO_SEMAPHORE


def _new_session() -> ResettableSession:
    browser = get_config("proxy.browser")
    if browser:
        return ResettableSession(impersonate=browser)
    return ResettableSession()


class VideoService:
    """Video generation service."""

    def __init__(self):
        self.timeout = None

    @staticmethod
    def _mode_flag(preset: str) -> str:
        mode_map = {
            "fun": "--mode=extremely-crazy",
            "normal": "--mode=normal",
            "spicy": "--mode=extremely-spicy-or-crazy",
        }
        return mode_map.get(preset, "--mode=custom")

    @staticmethod
    def _mode_value(preset: str) -> str:
        """Return raw mode value for videoGenModelConfig."""
        mode_map = {
            "fun": "extremely-crazy",
            "normal": "normal",
            "spicy": "extremely-spicy-or-crazy",
        }
        return mode_map.get(preset, "custom")

    @classmethod
    async def _build_message(cls, prompt: str, preset: str, nsfw_rewrite: bool = False) -> str:
        prompt_value = (prompt or "").strip()

        mode_flag = cls._mode_flag(preset)

        # 可选：自动脱敏改写（需前端传入 true 或者全局配置为 true 才启用）
        if preset in ("spicy", "fun") and (nsfw_rewrite or get_config("video.nsfw_rewrite")):
            from app.services.grok.services.nsfw_rewriter import NsfwPromptRewriter
            prompt_value = await NsfwPromptRewriter.rewrite(prompt_value, preset)

        return f"{prompt_value} {mode_flag}".strip()

    async def create_post(
        self,
        token: str,
        prompt: str,
        media_type: str = "MEDIA_POST_TYPE_VIDEO",
        media_url: str = None,
    ) -> str:
        """Create media post and return post ID."""
        try:
            if media_type == "MEDIA_POST_TYPE_IMAGE" and not media_url:
                raise ValidationException("media_url is required for image posts")

            prompt_value = prompt if media_type == "MEDIA_POST_TYPE_VIDEO" else ""
            media_value = media_url or ""

            async with _new_session() as session:
                async with _get_video_semaphore():
                    response = await MediaPostReverse.request(
                        session,
                        token,
                        media_type,
                        media_value,
                        prompt=prompt_value,
                    )

            post_id = response.json().get("post", {}).get("id", "")
            if not post_id:
                raise UpstreamException("No post ID in response")

            logger.info(f"Media post created: {post_id} (type={media_type})")
            return post_id

        except AppException:
            raise
        except Exception as e:
            logger.error(f"Create post error: {e}")
            raise UpstreamException(f"Create post error: {str(e)}")

    async def create_image_post(self, token: str, image_url: str) -> str:
        """Create image post and return post ID."""
        return await self.create_post(
            token, prompt="", media_type="MEDIA_POST_TYPE_IMAGE", media_url=image_url
        )

    async def generate(
        self,
        token: str,
        prompt: str,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution_name: str = "480p",
        preset: str = "normal",
        nsfw_rewrite: bool = False,
    ) -> AsyncGenerator[bytes, None]:
        """Generate video."""
        prompt_value = (prompt or "").strip()
        logger.info(
            f"Video generation: prompt='{prompt_value[:50]}...', ratio={aspect_ratio}, length={video_length}s, preset={preset}, nsfw_rewrite={nsfw_rewrite}"
        )
        post_id = await self.create_post(token, prompt_value)
        message = await self._build_message(prompt_value, preset, nsfw_rewrite=nsfw_rewrite)
        base_config = {
            "aspectRatio": aspect_ratio,
            "parentPostId": post_id,
            "resolutionName": resolution_name,
            "videoLength": video_length,
        }
        # DO NOT send mode in config — Grok's moderation checks this field.
        # Sending "extremely-spicy-or-crazy" here triggers instant block.
        # The --mode flag in message text bypasses moderation. See grok-video-nsfw-moderation.md
        model_config_override = {"modelMap": {"videoGenModelConfig": base_config}}

        async def _stream():
            session = _new_session()
            try:
                async with _get_video_semaphore():
                    stream_response = await AppChatReverse.request(
                        session,
                        token,
                        message=message,
                        model="grok-3",
                        tool_overrides={"videoGen": True},
                        model_config_override=model_config_override,
                    )
                    logger.info(f"Video generation started: post_id={post_id}")
                    async for line in stream_response:
                        yield line
            except Exception as e:
                try:
                    await session.close()
                except Exception:
                    pass
                logger.error(f"Video generation error: {e}")
                if isinstance(e, AppException):
                    raise
                raise UpstreamException(f"Video generation error: {str(e)}")

        return _stream()

    async def generate_from_image(
        self,
        token: str,
        prompt: str,
        image_url: str,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution: str = "480p",
        preset: str = "normal",
        nsfw_rewrite: bool = False,
    ) -> AsyncGenerator[bytes, None]:
        """Generate video from image."""
        prompt_value = (prompt or "").strip()
        logger.info(
            f"Image to video: prompt='{prompt_value[:50]}...', image={image_url[:80]}, nsfw_rewrite={nsfw_rewrite}"
        )
        post_id = await self.create_image_post(token, image_url)
        message = await self._build_message(prompt_value, preset, nsfw_rewrite=nsfw_rewrite)
        base_config = {
            "aspectRatio": aspect_ratio,
            "parentPostId": post_id,
            "resolutionName": resolution,
            "videoLength": video_length,
        }
        # DO NOT send mode in config — triggers Grok moderation. See grok-video-nsfw-moderation.md
        model_config_override = {"modelMap": {"videoGenModelConfig": base_config}}

        logger.info(f"i2v config: preset={preset!r}, message={message!r}, config={orjson.dumps(model_config_override).decode()}")

        async def _stream():
            session = _new_session()
            try:
                async with _get_video_semaphore():
                    stream_response = await AppChatReverse.request(
                        session,
                        token,
                        message=message,
                        model="grok-3",
                        tool_overrides={"videoGen": True},
                        model_config_override=model_config_override,
                    )
                    logger.info(f"Video generation started: post_id={post_id}")
                    async for line in stream_response:
                        yield line
            except Exception as e:
                try:
                    await session.close()
                except Exception:
                    pass
                logger.error(f"Video generation error: {e}")
                if isinstance(e, AppException):
                    raise
                raise UpstreamException(f"Video generation error: {str(e)}")

        return _stream()

    async def generate_from_post(
        self,
        token: str,
        prompt: str,
        parent_post_id: str,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution: str = "480p",
        preset: str = "normal",
        nsfw_rewrite: bool = False,
    ) -> AsyncGenerator[bytes, None]:
        """Generate video from an existing media post id."""
        prompt_value = (prompt or "").strip()
        post_id = (parent_post_id or "").strip()
        if not post_id:
            raise ValidationException("parent_post_id is required")

        logger.info(
            f"Post to video: prompt='{prompt_value[:50]}...', parent_post_id={post_id}, nsfw_rewrite={nsfw_rewrite}"
        )
        message = await self._build_message(prompt_value, preset, nsfw_rewrite=nsfw_rewrite)
        base_config = {
            "aspectRatio": aspect_ratio,
            "parentPostId": post_id,
            "resolutionName": resolution,
            "videoLength": video_length,
        }
        # DO NOT send mode in config — triggers Grok moderation. See grok-video-nsfw-moderation.md
        model_config_override = {"modelMap": {"videoGenModelConfig": base_config}}

        async def _stream():
            session = _new_session()
            try:
                async with _get_video_semaphore():
                    stream_response = await AppChatReverse.request(
                        session,
                        token,
                        message=message,
                        model="grok-3",
                        tool_overrides={"videoGen": True},
                        model_config_override=model_config_override,
                    )
                    logger.info(f"Video generation started: parent_post_id={post_id}")
                    async for line in stream_response:
                        yield line
            except Exception as e:
                try:
                    await session.close()
                except Exception:
                    pass
                logger.error(f"Video generation error: {e}")
                if isinstance(e, AppException):
                    raise
                raise UpstreamException(f"Video generation error: {str(e)}")

        return _stream()

    @staticmethod
    async def completions(
        model: str,
        messages: list,
        stream: bool = None,
        reasoning_effort: str | None = None,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution: str = "480p",
        preset: str = "normal",
        parent_post_id: Optional[str] = None,
        nsfw_rewrite: bool = False,
    ):
        """Video generation entrypoint."""
        # Get token via intelligent routing.
        token_mgr = await get_token_manager()
        await token_mgr.reload_if_stale()

        max_token_retries = 1  # No retry — fail fast for better UX
        last_error: Exception | None = None

        if reasoning_effort is None:
            show_think = get_config("app.thinking")
        else:
            show_think = reasoning_effort != "none"
        is_stream = stream if stream is not None else get_config("app.stream")

        # Extract content.
        from app.services.grok.services.chat import MessageExtractor
        from app.services.grok.utils.upload import UploadService

        prompt, file_attachments, image_attachments = MessageExtractor.extract(messages)
        parent_post_id = (parent_post_id or "").strip()

        if is_stream:
            async def _stream_generator():
                nonlocal last_error
                for attempt in range(max_token_retries):
                    pool_candidates = ModelService.pool_candidates_for_model(model)
                    token_info = token_mgr.get_token_for_video(
                        resolution=resolution,
                        video_length=video_length,
                        pool_candidates=pool_candidates,
                    )

                    if not token_info:
                        if last_error:
                            raise last_error
                        raise AppException(
                            message="No available tokens. Please try again later.",
                            error_type=ErrorType.RATE_LIMIT.value,
                            code="rate_limit_exceeded",
                            status_code=429,
                        )

                    token = token_info.token
                    if token.startswith("sso="):
                        token = token[4:]
                    pool_name = token_mgr.get_pool_name_for_token(token)
                    should_upscale = resolution == "720p" and pool_name == BASIC_POOL_NAME

                    try:
                        image_url = None
                        if image_attachments:
                            for attach_data in image_attachments:
                                if isinstance(attach_data, str) and attach_data.startswith("https://assets.grok.com/"):
                                    image_url = attach_data
                                    logger.info(f"Image already on assets.grok.com, skipping upload: {image_url}")
                                    break
                                upload_service = UploadService()
                                try:
                                    _, file_uri = await upload_service.upload_file(attach_data, token)
                                    image_url = f"https://assets.grok.com/{file_uri}"
                                    logger.info(f"Image uploaded for video: {image_url}")
                                finally:
                                    await upload_service.close()
                                break

                        service = VideoService()
                        if parent_post_id:
                            response = await service.generate_from_post(token, prompt, parent_post_id, aspect_ratio, video_length, resolution, preset, nsfw_rewrite=nsfw_rewrite)
                        elif image_url:
                            response = await service.generate_from_image(token, prompt, image_url, aspect_ratio, video_length, resolution, preset, nsfw_rewrite=nsfw_rewrite)
                        else:
                            response = await service.generate(token, prompt, aspect_ratio, video_length, resolution, preset, nsfw_rewrite=nsfw_rewrite)

                        processor = VideoStreamProcessor(model, token, show_think, upscale_on_finish=should_upscale)
                        wrapped_stream = wrap_stream_with_usage(processor.process(response), token_mgr, token, model)

                        # We must iterate the stream safely. If the stream yields an exception,
                        # we catch it and break the inner loop to trigger the outer token retry loop.
                        stream_failed = False
                        try:
                            async for chunk in wrapped_stream:
                                yield chunk
                        except UpstreamException as e:
                            last_error = e
                            if str(e) == "MODERATED_VIDEO":
                                logger.warning(f"Video moderated on token {token[:10]}... content blocked by Grok")
                                yield 'data: {"id":"sys","object":"chat.completion.chunk","created":0,"model":"video","choices":[{"index":0,"delta":{"content":"\\n**[内容审核]** Grok 拦截了此内容，请调整 prompt 或 preset 后重试。\\n"},"finish_reason":"stop"}]}\n\n'
                                return  # Stop immediately, don't retry — free accounts all have same moderation
                            elif rate_limited(e):
                                await token_mgr.mark_rate_limited(token)
                                logger.warning(f"Token {token[:10]}... rate limited (429), trying next token (attempt {attempt + 1}/{max_token_retries})")
                                stream_failed = True
                            else:
                                raise

                        if stream_failed:
                            continue
                        
                        # If we completed the stream successfully without exception, we are done
                        return

                    except Exception as e:
                        # Anything else outside the stream iteration breaks immediately
                        raise

                if last_error:
                    raise last_error
                raise AppException(
                    message="No available tokens. Please try again later.",
                    error_type=ErrorType.RATE_LIMIT.value,
                    code="rate_limit_exceeded",
                    status_code=429,
                )

            return _stream_generator()
        
        else:
            for attempt in range(max_token_retries):
                pool_candidates = ModelService.pool_candidates_for_model(model)
                token_info = token_mgr.get_token_for_video(
                    resolution=resolution,
                    video_length=video_length,
                    pool_candidates=pool_candidates,
                )

                if not token_info:
                    if last_error:
                        raise last_error
                    raise AppException(
                        message="No available tokens. Please try again later.",
                        error_type=ErrorType.RATE_LIMIT.value,
                        code="rate_limit_exceeded",
                        status_code=429,
                    )

                token = token_info.token
                if token.startswith("sso="):
                    token = token[4:]
                pool_name = token_mgr.get_pool_name_for_token(token)
                should_upscale = resolution == "720p" and pool_name == BASIC_POOL_NAME

                try:
                    image_url = None
                    if image_attachments:
                        for attach_data in image_attachments:
                            if isinstance(attach_data, str) and attach_data.startswith("https://assets.grok.com/"):
                                image_url = attach_data
                                break
                            upload_service = UploadService()
                            try:
                                _, file_uri = await upload_service.upload_file(attach_data, token)
                                image_url = f"https://assets.grok.com/{file_uri}"
                            finally:
                                await upload_service.close()
                            break

                    service = VideoService()
                    if parent_post_id:
                        response = await service.generate_from_post(token, prompt, parent_post_id, aspect_ratio, video_length, resolution, preset)
                    elif image_url:
                        response = await service.generate_from_image(token, prompt, image_url, aspect_ratio, video_length, resolution, preset)
                    else:
                        response = await service.generate(token, prompt, aspect_ratio, video_length, resolution, preset)

                    result = await VideoCollectProcessor(model, token, upscale_on_finish=should_upscale).process(response)
                    try:
                        model_info = ModelService.get(model)
                        effort = EffortType.HIGH if (model_info and model_info.cost.value == "high") else EffortType.LOW
                        await token_mgr.consume(token, effort)
                        logger.debug(f"Video completed, recorded usage (effort={effort.value})")
                    except Exception as e:
                        logger.warning(f"Failed to record video usage: {e}")
                    return result

                except UpstreamException as e:
                    last_error = e
                    if str(e) == "MODERATED_VIDEO":
                        logger.warning(f"Video moderated on token {token[:10]}... content blocked by Grok")
                        raise  # Stop immediately, don't retry — free accounts all have same moderation
                    if rate_limited(e):
                        await token_mgr.mark_rate_limited(token)
                        logger.warning(f"Token {token[:10]}... rate limited (429), trying next token (attempt {attempt + 1}/{max_token_retries})")
                        continue
                    raise

            if last_error:
                raise last_error
            raise AppException(
                message="No available tokens. Please try again later.",
                error_type=ErrorType.RATE_LIMIT.value,
                code="rate_limit_exceeded",
                status_code=429,
            )


class VideoStreamProcessor(BaseProcessor):
    """Video stream response processor."""

    def __init__(
        self,
        model: str,
        token: str = "",
        show_think: bool = None,
        upscale_on_finish: bool = False,
    ):
        super().__init__(model, token)
        self.response_id: Optional[str] = None
        self.think_opened: bool = False
        self.role_sent: bool = False

        self.show_think = bool(show_think)
        self.upscale_on_finish = bool(upscale_on_finish)

    @staticmethod
    def _extract_video_id(video_url: str) -> str:
        if not video_url:
            return ""
        match = re.search(r"/generated/([0-9a-fA-F-]{32,36})/", video_url)
        if match:
            return match.group(1)
        match = re.search(r"/([0-9a-fA-F-]{32,36})/generated_video", video_url)
        if match:
            return match.group(1)
        return ""

    async def _upscale_video_url(self, video_url: str) -> str:
        if not video_url or not self.upscale_on_finish:
            return video_url
        video_id = self._extract_video_id(video_url)
        if not video_id:
            logger.warning("Video upscale skipped: unable to extract video id")
            return video_url
        try:
            async with _new_session() as session:
                response = await VideoUpscaleReverse.request(
                    session, self.token, video_id
                )
            payload = response.json() if response is not None else {}
            hd_url = payload.get("hdMediaUrl") if isinstance(payload, dict) else None
            if hd_url:
                logger.info(f"Video upscale completed: {hd_url}")
                return hd_url
        except Exception as e:
            logger.warning(f"Video upscale failed: {e}")
        return video_url

    def _sse(self, content: str = "", role: str = None, finish: str = None) -> str:
        """Build SSE response."""
        delta = {}
        if role:
            delta["role"] = role
            delta["content"] = ""
        elif content:
            delta["content"] = content

        chunk = {
            "id": self.response_id or f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [
                {"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish}
            ],
        }
        return f"data: {orjson.dumps(chunk).decode()}\n\n"

    async def process(
        self, response: AsyncIterable[bytes]
    ) -> AsyncGenerator[str, None]:
        """Process video stream response."""
        idle_timeout = get_config("video.stream_timeout")
        video_yielded = False
        _conversation_id = ""

        try:
            async for line in _with_idle_timeout(response, idle_timeout, self.model):
                line = _normalize_line(line)
                if not line:
                    continue
                try:
                    data = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue

                # Capture conversationId from first response line
                if not _conversation_id:
                    conv = data.get("result", {}).get("conversation", {})
                    if conv_id := conv.get("conversationId"):
                        _conversation_id = conv_id

                resp = data.get("result", {}).get("response", {})
                is_thinking = bool(resp.get("isThinking"))

                if rid := resp.get("responseId"):
                    self.response_id = rid

                if not self.role_sent:
                    yield self._sse(role="assistant")
                    self.role_sent = True

                if token := resp.get("token"):
                    if is_thinking:
                        if not self.show_think:
                            continue
                        if not self.think_opened:
                            yield self._sse("<think>\n")
                            self.think_opened = True
                    else:
                        if self.think_opened:
                            yield self._sse("\n</think>\n")
                            self.think_opened = False
                    yield self._sse(token)
                    continue

                if video_resp := resp.get("streamingVideoGenerationResponse"):
                    progress = video_resp.get("progress", 0)

                    if is_thinking:
                        if not self.show_think:
                            continue
                        if not self.think_opened:
                            yield self._sse("<think>\n")
                            self.think_opened = True
                    else:
                        if self.think_opened:
                            yield self._sse("\n</think>\n")
                            self.think_opened = False
                    if self.show_think:
                        yield self._sse(f"正在生成视频中，当前进度{progress}%\n")

                    if progress == 100:
                        video_url = video_resp.get("videoUrl", "")
                        thumbnail_url = video_resp.get("thumbnailImageUrl", "")
                        video_id = video_resp.get("videoId", "")
                        video_post_id = video_resp.get("videoPostId", "")
                        moderated = video_resp.get("moderated", None)
                        image_ref = video_resp.get("imageReference", "")
                        logger.info(
                            f"Video progress 100%: videoUrl={video_url!r}, videoId={video_id!r}, "
                            f"videoPostId={video_post_id!r}, moderated={moderated!r}"
                        )

                        if moderated is True:
                            logger.error(f"Video generation intercepted by moderation! {video_resp}")
                            raise UpstreamException("MODERATED_VIDEO")

                        # Fallback: when moderated=true, videoUrl is cleared
                        # but videoId + imageReference are preserved.
                        # Construct URL from imageReference pattern:
                        #   https://assets.grok.com/users/{userId}/{postId}/content
                        if not video_url and (video_id or video_post_id) and image_ref:
                            vid = video_id or video_post_id
                            # Extract userId from imageReference
                            m = re.match(
                                r"https://assets\.grok\.com/users/([^/]+)/",
                                image_ref,
                            )
                            if m:
                                user_id = m.group(1)
                                video_url = f"https://assets.grok.com/users/{user_id}/generated/{vid}/generated_video.mp4"
                                logger.info(
                                    f"Constructed video URL from videoId: {video_url}"
                                )

                        if self.think_opened:
                            yield self._sse("\n</think>\n")
                            self.think_opened = False

                        if video_url:
                            # Store token + conversationId for extend support
                            vid_for_cache = video_post_id or self._extract_video_id(video_url)
                            if vid_for_cache:
                                store_video_context(vid_for_cache, self.token, _conversation_id)
                                logger.info(f"Cached context for video extend: vid={vid_for_cache}, conv={_conversation_id}, token_hash={hash(self.token)}, token_prefix={self.token[:30]}...")

                            if self.upscale_on_finish:
                                yield self._sse("正在对视频进行超分辨率\n")
                                video_url = await self._upscale_video_url(video_url)
                            dl_service = self._get_dl()
                            rendered = await dl_service.render_video(
                                video_url, self.token, thumbnail_url
                            )
                            yield self._sse(rendered)

                            # Emit video_post_id as metadata for extend support
                            if vid_for_cache:
                                yield self._sse(f"\n<!-- video_post_id:{vid_for_cache} -->\n")

                            video_yielded = True

                            logger.info(f"Video generated: {video_url}")
                        else:
                            logger.error(f"Video URL missing at 100%: {video_resp}")
                            raise UpstreamException("MODERATED_VIDEO")
                            
                    continue

            if not video_yielded:
                logger.error("Video stream ended without yielding a videoURL! Assuming silently moderated/rejected.")
                raise UpstreamException("MODERATED_VIDEO")

            if self.think_opened:
                yield self._sse("</think>\n")
            yield self._sse(finish="stop")
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            logger.debug(
                "Video stream cancelled by client", extra={"model": self.model}
            )
        except StreamIdleTimeoutError as e:
            raise UpstreamException(
                message=f"Video stream idle timeout after {e.idle_seconds}s",
                status_code=504,
                details={
                    "error": str(e),
                    "type": "stream_idle_timeout",
                    "idle_seconds": e.idle_seconds,
                },
            )
        except RequestsError as e:
            if _is_http2_error(e):
                logger.warning(
                    f"HTTP/2 stream error in video: {e}", extra={"model": self.model}
                )
                raise UpstreamException(
                    message="Upstream connection closed unexpectedly",
                    status_code=502,
                    details={"error": str(e), "type": "http2_stream_error"},
                )
            logger.error(
                f"Video stream request error: {e}", extra={"model": self.model}
            )
            raise UpstreamException(
                message=f"Upstream request failed: {e}",
                status_code=502,
                details={"error": str(e)},
            )
        except Exception as e:
            logger.error(
                f"Video stream processing error: {e}",
                extra={"model": self.model, "error_type": type(e).__name__},
            )
            raise
        finally:
            await self.close()


class VideoCollectProcessor(BaseProcessor):
    """Video non-stream response processor."""

    def __init__(self, model: str, token: str = "", upscale_on_finish: bool = False):
        super().__init__(model, token)
        self.upscale_on_finish = bool(upscale_on_finish)

    @staticmethod
    def _extract_video_id(video_url: str) -> str:
        if not video_url:
            return ""
        match = re.search(r"/generated/([0-9a-fA-F-]{32,36})/", video_url)
        if match:
            return match.group(1)
        match = re.search(r"/([0-9a-fA-F-]{32,36})/generated_video", video_url)
        if match:
            return match.group(1)
        return ""

    async def _upscale_video_url(self, video_url: str) -> str:
        if not video_url or not self.upscale_on_finish:
            return video_url
        video_id = self._extract_video_id(video_url)
        if not video_id:
            logger.warning("Video upscale skipped: unable to extract video id")
            return video_url
        try:
            async with _new_session() as session:
                response = await VideoUpscaleReverse.request(
                    session, self.token, video_id
                )
            payload = response.json() if response is not None else {}
            hd_url = payload.get("hdMediaUrl") if isinstance(payload, dict) else None
            if hd_url:
                logger.info(f"Video upscale completed: {hd_url}")
                return hd_url
        except Exception as e:
            logger.warning(f"Video upscale failed: {e}")
        return video_url

    async def process(self, response: AsyncIterable[bytes]) -> dict[str, Any]:
        """Process and collect video response."""
        response_id = ""
        content = ""
        idle_timeout = get_config("video.stream_timeout")
        _conversation_id = ""

        try:
            async for line in _with_idle_timeout(response, idle_timeout, self.model):
                line = _normalize_line(line)
                if not line:
                    continue
                try:
                    data = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue

                # Capture conversationId for extend support
                if not _conversation_id:
                    conv = data.get("result", {}).get("conversation", {})
                    if conv_id := conv.get("conversationId"):
                        _conversation_id = conv_id

                resp = data.get("result", {}).get("response", {})

                if video_resp := resp.get("streamingVideoGenerationResponse"):
                    if video_resp.get("progress") == 100:
                        response_id = resp.get("responseId", "")
                        video_url = video_resp.get("videoUrl", "")
                        thumbnail_url = video_resp.get("thumbnailImageUrl", "")
                        moderated = video_resp.get("moderated", None)

                        if moderated is True:
                            logger.error(f"Video generation intercepted by moderation! {video_resp}")
                            raise UpstreamException("MODERATED_VIDEO")

                        # Fallback: construct URL from videoId when moderated
                        if not video_url:
                            vid = video_resp.get("videoId", "") or video_resp.get("videoPostId", "")
                            image_ref = video_resp.get("imageReference", "")
                            if vid and image_ref:
                                m = re.match(r"https://assets\.grok\.com/users/([^/]+)/", image_ref)
                                if m:
                                    video_url = f"https://assets.grok.com/users/{m.group(1)}/generated/{vid}/generated_video.mp4"
                                    logger.info(f"Constructed video URL from videoId: {video_url}")

                        if video_url:
                            # Store token for extend support
                            vid_for_cache = (
                                video_resp.get("videoPostId", "")
                                or video_resp.get("videoId", "")
                                or self._extract_video_id(video_url)
                            )
                            if vid_for_cache:
                                store_video_context(vid_for_cache, self.token, _conversation_id)
                                logger.info(f"Cached context for video extend: vid={vid_for_cache}, conv={_conversation_id}, token_hash={hash(self.token)}, token_prefix={self.token[:30]}...")

                            if self.upscale_on_finish:
                                video_url = await self._upscale_video_url(video_url)
                            dl_service = self._get_dl()
                            content = await dl_service.render_video(
                                video_url, self.token, thumbnail_url
                            )
                            # Append video_post_id metadata for extend support
                            if vid_for_cache:
                                content += f"\n<!-- video_post_id:{vid_for_cache} -->\n"
                            logger.info(f"Video generated: {video_url}")

        except asyncio.CancelledError:
            logger.debug(
                "Video collect cancelled by client", extra={"model": self.model}
            )
        except StreamIdleTimeoutError as e:
            logger.warning(
                f"Video collect idle timeout: {e}", extra={"model": self.model}
            )
            raise UpstreamException(
                message=f"Video collect idle timeout after {e.idle_seconds}s",
                status_code=504,
                details={"error": str(e), "type": "stream_idle_timeout", "idle_seconds": e.idle_seconds},
            )
        except RequestsError as e:
            if _is_http2_error(e):
                logger.warning(
                    f"HTTP/2 stream error in video collect: {e}",
                    extra={"model": self.model},
                )
                raise UpstreamException(
                    message="Upstream connection closed unexpectedly",
                    status_code=502,
                    details={"error": str(e), "type": "http2_stream_error"},
                )
            else:
                logger.error(
                    f"Video collect request error: {e}", extra={"model": self.model}
                )
                raise UpstreamException(
                    message=f"Upstream request failed: {e}",
                    status_code=502,
                    details={"error": str(e)},
                )
        except Exception as e:
            logger.error(
                f"Video collect processing error: {e}",
                extra={"model": self.model, "error_type": type(e).__name__},
            )
            raise
        finally:
            await self.close()

        return {
            "id": response_id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "refusal": None,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


__all__ = ["VideoService"]
