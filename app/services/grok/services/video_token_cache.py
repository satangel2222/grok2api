"""
Independent video token cache module.

Maps video_post_id -> (token, timestamp) so that video extend
can reuse the same Grok account that generated the original video.

This module is intentionally standalone — no imports from video.py
or video_extend.py — so that reverting either file never causes
ImportError cascades.
"""

import time
from typing import Optional

_cache: dict[str, tuple[str, float]] = {}
_TTL = 3600  # 1 hour


def store_video_token(reference_id: str, token: str) -> None:
    """Store token used for video generation, keyed by video_post_id."""
    _cache[reference_id] = (token, time.time())
    # Evict expired entries
    cutoff = time.time() - _TTL
    for k in list(_cache.keys()):
        if _cache[k][1] < cutoff:
            del _cache[k]


def get_video_token(reference_id: str) -> Optional[str]:
    """Retrieve the token that generated a video by reference_id."""
    entry = _cache.get(reference_id)
    if entry and (time.time() - entry[1]) < _TTL:
        return entry[0]
    return None
