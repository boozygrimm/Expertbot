"""Image generation: Cloudflare FLUX.2 first, anonymous OVHcloud SDXL backup.

Provider errors and prompts are never logged. No automatic retries: a timed-out
request can still consume quota, and a retry could produce duplicate work.
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
    async for chunk in response.content.iter_chunked(65536):
        chunks.extend(chunk)
        if len(chunks) > limit:
            raise ImageProviderError('response too large')
    return bytes(chunks)


def _check_status(status, *, primary=False):
    if status == 200:
        return
    # Do not use a different provider to work around content/policy or invalid
    # request rejections. 401 means the primary is not accessible; 429, 408 and
    # 5xx are quota/rate/service failures and can use the approved backup.
    fallback = not primary or status in (401, 408, 429) or 500 <= status <= 599
    raise ImageProviderError(f'HTTP {status}', fallback=fallback)


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
                _check_status(response.status, primary=True)
                raw = await _read_bounded(response, MAX_RESPONSE_BYTES)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise ImageProviderError('network error') from None

    # FLUX.2 returns JSON with base64 image. Also accept raw JPEG/PNG, as in
    # the standalone test, while rejecting HTML/error bodies served with 200.
    if raw.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')):
        return _validate_image(raw)
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict) and payload.get('success') is False:
            raise ImageProviderError('request rejected', fallback=False)
        encoded = payload['result']['image']
        if not isinstance(encoded, str) or len(encoded) > MAX_RESPONSE_BYTES:
            raise ValueError('invalid payload')
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, KeyError, binascii.Error):
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
