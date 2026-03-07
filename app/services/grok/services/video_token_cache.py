"""
Independent video token cache module.

Maps video_post_id -> (token, conversation_id, timestamp) so that
video extend can reuse the same Grok account AND conversation.

This module is intentionally standalone — no imports from video.py
or video_extend.py — so that reverting either file never causes
ImportError cascades.
"""

import time
from dataclasses import dataclass
from typing import Optional

_TTL = 3600  # 1 hour


@dataclass
class VideoContext:
    token: str
    conversation_id: str
    timestamp: float


_cache: dict[str, VideoContext] = {}


def store_video_context(
    reference_id: str, token: str, conversation_id: str = ""
) -> None:
    """Store token + conversation for video generation."""
    _cache[reference_id] = VideoContext(
        token=token, conversation_id=conversation_id, timestamp=time.time()
    )
    # Evict expired entries
    cutoff = time.time() - _TTL
    for k in list(_cache.keys()):
        if _cache[k].timestamp < cutoff:
            del _cache[k]


def get_video_context(reference_id: str) -> Optional[VideoContext]:
    """Get full context (token + conversation_id) for a video."""
    entry = _cache.get(reference_id)
    if entry and (time.time() - entry.timestamp) < _TTL:
        return entry
    return None


# Backward-compatible aliases
def store_video_token(reference_id: str, token: str) -> None:
    store_video_context(reference_id, token)


def get_video_token(reference_id: str) -> Optional[str]:
    ctx = get_video_context(reference_id)
    return ctx.token if ctx else None
