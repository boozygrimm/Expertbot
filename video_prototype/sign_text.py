"""Small, dependency-free contract for optional humorous sign text."""
import json
import re
import unicodedata

MAX_SIGN_LENGTH = 22
SIGN_CHARS = re.compile(r'^[А-ЯЁA-Z0-9 !?,.\-]+$')


def normalize_sign(value):
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize('NFC', value).upper()
    value = re.sub(r'\s+', ' ', value).strip()
    if (not value or len(value) > MAX_SIGN_LENGTH or not SIGN_CHARS.fullmatch(value)
            or not any(c.isalnum() for c in value)):
        return None
    words = value.split()
    if len(words) > 3 or any(len(word) > 12 for word in words):
        return None
    return value


def parse_generated(value):
    """Return speech and optional sign; never append structured fields to speech.

    Incomplete or invalid sign is safely dropped. If structured output is so
    broken that the speech cannot be isolated, fail closed instead of reading
    instructions or the sign aloud. Plain old-model output remains compatible.
    """
    if not isinstance(value, str):
        return '', None
    raw = value.strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.I)
        raw = re.sub(r'\s*```\s*$', '', raw)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        speech = data.get('speech')
        return (speech.strip() if isinstance(speech, str) else '', normalize_sign(data.get('sign')))

    # Recover a complete JSON speech string even if the second field is broken.
    match = re.search(r'"speech"\s*:\s*("(?:\\.|[^"\\])*")', raw, flags=re.I)
    if match:
        try:
            speech = json.loads(match.group(1))
            return (speech.strip() if isinstance(speech, str) else '', None)
        except ValueError:
            return '', None
    # A common non-JSON response is still unambiguous if explicitly labeled.
    match = re.match(r'\s*(?:ОТВЕТ|SPEECH)\s*:\s*(.*?)(?:\n\s*(?:ТАБЛИЧКА|SIGN)\s*:|\Z)', raw,
                     flags=re.I | re.S)
    if match:
        return match.group(1).strip(), None
    if (not raw or raw.startswith(('{', '[', '<')) or
            re.search(r'(?i)(?:"(?:speech|sign)"\s*:|\b(?:ТАБЛИЧКА|SIGN)\s*:|</?(?:speech|sign)>)', raw)):
        return '', None
    return raw, None
