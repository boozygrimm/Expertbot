"""Image generation: Cloudflare FLUX.2 first, anonymous OVHcloud SDXL backup.

Only sanitized provider status, numeric codes and categories are
logged; provider messages, prompts and credentials are never logged. No automatic
retries: a timed-out request can still consume quota and duplicate work.
"""
import asyncio
import base64
import binascii
import io
import json
import logging
import os
import re
import time

import aiohttp
from PIL import Image

logger = logging.getLogger(__name__)
CLOUDFLARE_MODEL = '@cf/black-forest-labs/flux-2-klein-4b'
CLOUDFLARE_URL = 'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/' + CLOUDFLARE_MODEL
OVH_URL = 'https://stable-diffusion-xl.endpoints.kepler.ai.cloud.ovh.net/api/text2image'
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_BYTES = 17 * 1024 * 1024
MAX_ERROR_BYTES = 16 * 1024
# The documented limit is 2 requests/minute/IP/model. This is per process;
# other processes or services sharing the IP can consume the same allowance.
OVH_INTERVAL_SECONDS = 35
_ovh_lock = asyncio.Lock()
_last_ovh_request = None


class ImageProviderError(Exception):
    def __init__(self, reason, *, fallback=True):
        super().__init__(reason)
        self.fallback = fallback


def _validate_image(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageProviderError('invalid image')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('JPEG', 'PNG') or image.width * image.height > 4096 * 4096:
                raise ImageProviderError('invalid image')
            image.verify()
    except ImageProviderError:
        raise
    except Exception:
        # Malformed image files can make different Pillow plugins raise
        # different decoding exceptions; none should crash the bot.
        raise ImageProviderError('invalid image') from None
    return data


async def _read_bounded(response, limit):
    chunks = bytearray()
    async for chunk in response.content.iter_chunked(min(65536, limit + 1)):
        if len(chunk) > limit - len(chunks):
            raise ImageProviderError('response too large')
        chunks.extend(chunk)
    return bytes(chunks)


def _check_status(status):
    if status != 200:
        raise ImageProviderError(f'HTTP {status}')


# Codes from the Workers AI error reference: model unavailable/deprecated,
# exhausted quota and insufficient capacity. A 3030 inference error is NOT a
# moderation code: it can also describe unrelated failures.
_CLOUDFLARE_AVAILABILITY_CODES = frozenset({5007, 5028, 3036, 3040})
_FLAGGED_MESSAGES = ('your output has been flagged', 'your input has been flagged')


def _cloudflare_failure(status, payload=None, *, body_state='parsed'):
    """Build a safe error without retaining or logging untrusted provider text.

    For ambiguous 400s (and success:false at 200), fail closed. An explicit
    flagged message always wins over availability codes, including on 429/5xx.
    """
    errors = payload.get('errors') if isinstance(payload, dict) else None
    codes = []
    complete = isinstance(errors, list) and 0 < len(errors) <= 8
    flagged = False
    if isinstance(errors, list):
        for item in errors[:8]:
            if not isinstance(item, dict):
                complete = False
                continue
            code = item.get('code')
            if type(code) is int and 0 <= code <= 99999:
                codes.append(code)
            else:
                complete = False
            message = item.get('message')
            if isinstance(message, str) and any(text in message.casefold() for text in _FLAGGED_MESSAGES):
                flagged = True
    if flagged:
        category, fallback = 'flagged', False
    elif status == 403:
        category, fallback = 'forbidden', False
    elif isinstance(errors, list) and len(errors) > 8:
        category, fallback = 'unknown_rejection', False
    elif status in (200, 400, 410) and complete and codes and all(
        code in _CLOUDFLARE_AVAILABILITY_CODES for code in codes
    ):
        category, fallback = 'availability', True
    elif status in (401, 408, 429) or 500 <= status <= 599:
        category, fallback = 'http_availability', True
    else:
        category, fallback = 'unknown_rejection', False
    # body_state and category are selected internally, never supplied by a server.
    codes_text = ','.join(str(code) for code in codes) or 'none'
    return ImageProviderError(
        f'HTTP {status} category={category} codes={codes_text} body={body_state}',
        fallback=fallback,
    )


async def _cloudflare_http_error(response):
    try:
        raw = await _read_bounded(response, MAX_ERROR_BYTES)
    except ImageProviderError:
        return _cloudflare_failure(response.status, body_state='too_large')
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return _cloudflare_failure(response.status, body_state='unreadable')
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return _cloudflare_failure(response.status, body_state='malformed')
    return _cloudflare_failure(response.status, payload)


async def fetch_cloudflare(prompt):
    account = os.getenv('CLOUDFLARE_ACCOUNT_ID', '').strip()
    token = os.getenv('CLOUDFLARE_AI_TOKEN', '').strip()
    if not re.fullmatch(r'[0-9a-fA-F]{32}', account) or not token:
        raise ImageProviderError('not configured')
    form = aiohttp.FormData()
    # A content type on a field forces multipart even without file uploads.
    form.add_field('prompt', prompt, content_type='text/plain')
    form.add_field('width', '1024')
    form.add_field('height', '1024')
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
            async with session.post(CLOUDFLARE_URL.format(account=account), data=form,
                                    headers={'Authorization': f'Bearer {token}'},
                                    allow_redirects=False) as response:
                if response.status != 200:
                    raise await _cloudflare_http_error(response)
                try:
                    raw = await _read_bounded(response, MAX_RESPONSE_BYTES)
                except ImageProviderError:
                    # This might be an oversized success:false rejection.
                    raise ImageProviderError('HTTP 200 category=unknown_rejection codes=none body=too_large',
                                             fallback=False) from None
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        raise ImageProviderError('network error') from None

    # FLUX.2 returns JSON with base64 image. Also accept raw JPEG/PNG, as in
    # the standalone test, while rejecting HTML/error bodies served with 200.
    if raw.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')):
        return _validate_image(raw)
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict) and payload.get('success') is False:
            raise _cloudflare_failure(200, payload)
        encoded = payload['result']['image']
        if not isinstance(encoded, str) or len(encoded) > MAX_RESPONSE_BYTES:
            raise ValueError('invalid payload')
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, KeyError, binascii.Error, RecursionError):
        raise ImageProviderError('invalid response') from None
    return _validate_image(image)


async def fetch_ovh(prompt):
    global _last_ovh_request
    # Lock and timestamp apply even to failed requests: they may have reached OVH.
    async with _ovh_lock:
        if _last_ovh_request is not None:
            wait = OVH_INTERVAL_SECONDS - (time.monotonic() - _last_ovh_request)
            if wait > 0:
                await asyncio.sleep(wait)
        _last_ovh_request = time.monotonic()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
                async with session.post(OVH_URL, json={'prompt': prompt},
                                        headers={'Accept': 'application/octet-stream'},
                                        allow_redirects=False) as response:
                    _check_status(response.status)
                    raw = await _read_bounded(response, MAX_IMAGE_BYTES)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise ImageProviderError('network error') from None
    return _validate_image(raw)


async def generate_image(prompt):
    try:
        return await fetch_cloudflare(prompt)
    except ImageProviderError as error:
        logger.warning('Cloudflare image unavailable: %s', error)
        if not error.fallback:
            return None
    try:
        return await fetch_ovh(prompt)
    except ImageProviderError as error:
        logger.warning('OVHcloud image unavailable: %s', error)
        return None
