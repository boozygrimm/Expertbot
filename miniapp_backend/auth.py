"""Verify Telegram WebApp initData; never trust initDataUnsafe or a caller-supplied ID."""
import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl


class InvalidInitData(ValueError):
    """The supplied initialization data cannot be trusted."""


def verify_init_data(raw: str, bot_token: str, *, now: int | None = None, max_age: int = 900) -> int:
    if not isinstance(raw, str) or not raw or len(raw) > 8192 or not bot_token or max_age <= 0:
        raise InvalidInitData('invalid init data')
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, encoding='utf-8', errors='strict', max_num_fields=64)
        fields = dict(pairs)
        if len(fields) != len(pairs) or any(not k or '\n' in k or '\r' in k or '\n' in v or '\r' in v for k, v in pairs):
            raise ValueError('ambiguous fields')
        supplied = fields.pop('hash')
        if not re.fullmatch(r'[0-9a-f]{64}', supplied):
            raise ValueError('invalid hash')
        check = '\n'.join(f'{key}={value}' for key, value in sorted(fields.items()))
        secret = hmac.new(b'WebAppData', bot_token.encode('utf-8'), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ValueError('invalid signature')
        date = fields['auth_date']
        if not re.fullmatch(r'[0-9]{1,12}', date):
            raise ValueError('invalid date')
        timestamp = int(date)
        current = int(time.time()) if now is None else now
        if timestamp > current + 30 or timestamp < current - max_age:
            raise ValueError('expired')
        user = json.loads(fields['user'])
        uid = user.get('id') if isinstance(user, dict) else None
        if type(uid) is not int or uid <= 0 or uid > 2**63 - 1:
            raise ValueError('invalid user')
        return uid
    except (ValueError, KeyError, TypeError, UnicodeError, OverflowError) as exc:
        raise InvalidInitData('invalid init data') from exc
