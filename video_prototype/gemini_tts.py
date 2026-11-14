"""Bounded Gemini TTS attempts for the InstaBox video pipeline.

Only called when explicitly enabled. Never log request text, keys, or API bodies.
"""
import base64
import io
import json
import re
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

MODEL = 'gemini-3.8-flash-tts'
VOICE = 'Algenib'
STYLE = 'Говори по-русски, низким мужским голосом, выразительно и с лёгким сарказмом.'
API = f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent'
MAX_RESPONSE = 12 * 1024 * 1024
MAX_PCM = 60 * 24000 * 2
REQUEST_TIMEOUT = 25
TOTAL_BUDGET = 45


class TTSFailure(Exception):
    """A safe failure message, never including request/response contents."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_keys(raw):
    """Preserve ordering, omit duplicates and empty entries."""
    return list(dict.fromkeys(key.strip() for key in raw.split(',') if key.strip()))


def validated_audio(result):
    """Decode one complete audio part into valid mono 16-bit WAV (<60 s)."""
    try:
        candidate = result['candidates'][0]
        if candidate.get('finishReason', 'STOP') != 'STOP':
            raise ValueError('incomplete candidate')
        parts = candidate['content']['parts']
        items = [p['inlineData'] for p in parts if isinstance(p, dict) and 'inlineData' in p]
        if len(items) != 1:
            raise ValueError('expected one audio part')
        item = items[0]
        mime = item.get('mimeType', '').lower()
        data = base64.b64decode(item['data'], validate=True)
        if not data or len(data) > MAX_PCM + 65536:
            raise ValueError('bad size')
        if data.startswith(b'RIFF') and data[8:12] == b'WAVE':
            wav_bytes = data
        elif mime.startswith(('audio/l16', 'audio/pcm')):
            rate = re.search(r'(?:^|;)\s*rate\s*=\s*(\d+)', mime)
            channels = re.search(r'(?:^|;)\s*channels\s*=\s*(\d+)', mime)
            if (rate and int(rate.group(1)) != 24000) or (channels and int(channels.group(1)) != 1) or len(data) % 2:
                raise ValueError('unsupported PCM')
            stream = io.BytesIO()
            with wave.open(stream, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(data)
            wav_bytes = stream.getvalue()
        else:
            raise ValueError('unsupported format')
        with wave.open(io.BytesIO(wav_bytes), 'rb') as wav:
            rate, count = wav.getframerate(), wav.getnframes()
            if (wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != 'NONE'
                    or rate <= 0 or count <= 0 or count / rate >= 60):
                raise ValueError('unsupported WAV')
            frames = wav.readframes(count)
            if len(frames) != count * 2 or not any(frames):
                raise ValueError('truncated or silent WAV')
        return wav_bytes, count / rate
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, wave.Error, EOFError):
        raise TTSFailure('неполный или некорректный аудиоответ') from None


def request_one(key, text, timeout):
    body = {'contents': [{'role': 'user', 'parts': [{'text': text,
             'speech_metadata': {'style': STYLE}}]}],
            'generationConfig': {'responseModalities': ['AUDIO'],
                                 'speechConfig': {'voiceConfig': {'voice': VOICE}}}}
    request = urllib.request.Request(API, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={'Content-Type': 'application/json', 'x-goog-api-key': key}, method='POST')
    try:
        # Do not forward the key to a redirect destination.
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise TTSFailure('слишком большой ответ Gemini')
        return validated_audio(json.loads(raw))
    except urllib.error.HTTPError as exc:
        raise TTSFailure(f'HTTP {exc.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, RecursionError):
        raise TTSFailure('ошибка связи или формата Gemini') from None


def synthesize(text, output, keys, start_index=0, *, budget=TOTAL_BUDGET):
    """Rotate initial key, then try other keys once until the deadline for starting attempts."""
    if not keys:
        raise TTSFailure('нет ключей Gemini')
    if budget <= 0:
        raise TTSFailure('нет времени на Gemini')
    ordered = keys[start_index % len(keys):] + keys[:start_index % len(keys)]
    deadline = time.monotonic() + budget
    for key in ordered:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            data, duration = request_one(key, text, min(REQUEST_TIMEOUT, remaining))
            # A fresh clip directory is used per job; only publish validated WAV.
            temp = Path(output).with_suffix('.wav.tmp')
            temp.write_bytes(data)
            temp.replace(output)
            print(f'Gemini TTS готов: {duration:.1f} с аудио', flush=True)
            return
        except TTSFailure as exc:
            print(f'Gemini TTS не сработал ({exc}); пробуем следующий ключ', flush=True)
    raise TTSFailure('ключи или время попыток Gemini исчерпаны')
