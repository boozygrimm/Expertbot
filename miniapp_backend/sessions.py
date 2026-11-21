"""Single-process, short-lived test sessions. Not a distributed session store."""
import asyncio
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qsl

from .auth import verify_init_data


class ReplayError(ValueError):
    pass


class CapacityError(ValueError):
    pass


def _init_fingerprint(raw):
    # verify_init_data must run first. Normalize parameter order and percent encoding
    # so the same signed initData cannot be replayed using a different query string.
    pairs = sorted(parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=64))
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(',', ':')).encode()).digest()


def _token_digest(token):
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        return None
    try:
        return hashlib.sha256(token.encode('ascii')).digest()
    except UnicodeError:
        return None


class SessionStore:
    def __init__(self, bot_token, allowed_users, *, ttl=600, max_entries=1024, clock=time.monotonic):
        if not bot_token or not allowed_users or any(type(uid) is not int or uid <= 0 for uid in allowed_users):
            raise ValueError('a token and explicit allowlist are required')
        if ttl <= 0 or max_entries <= 0:
            raise ValueError('invalid session limits')
        self._token = bot_token
        self._allowed = frozenset(allowed_users)
        self._ttl = ttl
        self._max_entries = max_entries
        self._clock = clock
        self._sessions = {}
        self._used = {}
        self._lock = asyncio.Lock()

    def _purge(self, now):
        self._sessions = {key: value for key, value in self._sessions.items() if value[1] > now}
        self._used = {key: expires for key, expires in self._used.items() if expires > now}

    async def exchange(self, raw):
        user_id = verify_init_data(raw, self._token)
        if user_id not in self._allowed:
            raise PermissionError('not permitted')
        fingerprint = _init_fingerprint(raw)
        async with self._lock:
            now = self._clock()
            self._purge(now)
            if fingerprint in self._used:
                raise ReplayError('already exchanged')
            if len(self._used) >= self._max_entries or len(self._sessions) >= self._max_entries:
                raise CapacityError('session capacity reached')
            token = secrets.token_urlsafe(32)
            self._sessions[_token_digest(token)] = (user_id, now + self._ttl)
            # initData may be up to 30 seconds in the future and is accepted for
            # 900 seconds; retaining 931 seconds prevents reuse while still valid.
            self._used[fingerprint] = now + 931
            return token, self._ttl

    async def resolve(self, token):
        digest = _token_digest(token)
        if digest is None:
            return None
        async with self._lock:
            self._purge(self._clock())
            entry = self._sessions.get(digest)
            return entry[0] if entry else None

    async def revoke(self, token):
        digest = _token_digest(token)
        if digest is None:
            return
        async with self._lock:
            self._sessions.pop(digest, None)
