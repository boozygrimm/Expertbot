"""Shared explicit triggers for the video-note feature."""
import re

EX_VIDEO_PATTERN = r'(?i)^экс(?:перт)?(?:\s*,\s*|\s+)кружок(?=\s|$)'
EX_VIDEO_RE = re.compile(EX_VIDEO_PATTERN)
COMMAND_RE = re.compile(r'(?i)^/bender(?:@[a-z0-9_]+)?(?=\s|$)')


def parse_video_request(text):
    """Return the query, including an empty query, or None for non-triggers."""
    text = (text or '').strip()
    match = COMMAND_RE.match(text) or EX_VIDEO_RE.match(text)
    return text[match.end():].strip() if match else None
