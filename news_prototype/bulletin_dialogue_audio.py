"""Experimental one-request-per-news dialogue audio; not yet used by worker.

The speaker audio is generated jointly within each news item, using the existing
Gemini key rotation. Each full item remains a separate checked WAV, allowing
exact picture changes between items. This module does not identify speaker turn
boundaries, verify the spoken text, render video or deliver anything. Call only
after explicit owner confirmation, with a fresh private output directory.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request
import wave

from news_prototype import bulletin_contract, podcast_audio_trial, podcast_video_trial
from video_prototype import gemini_tts

API = gemini_tts.API
MAX_RESPONSE = 24 * 1024 * 1024
MAX_AUDIO = 18 * 1024 * 1024
MAX_SECONDS = 360
VOICES = {
    'Бендер': ('Bender', 'Algenib', 'По-русски, выразительно, с лёгким сарказмом.'),
    'Киберсенсей': ('Sensei', 'Puck', 'По-русски спокойно, тепло и доброжелательно.'),
}


def request_body(section: dict) -> dict:
    parts = []
    for turn in section['turns']:
        name, _, style = VOICES[turn['speaker']]
        parts.append({'text': turn['text'], 'speechMetadata': {'speaker': name, 'style': style}})
    return {'contents': [{'role': 'user', 'parts': parts}],
            'generationConfig': {'responseModalities': ['AUDIO'],
                'speechConfig': {'multiSpeakerVoiceConfig': {'speakerVoiceConfigs': [
                    {'speaker': name, 'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': voice}}}
                    for name, voice, _ in VOICES.values()]}}}}


def decode_audio(result) -> tuple[bytes, float]:
    """Validate complete WAV or linear PCM; never trim output to fit a limit."""
    try:
        candidate = result['candidates'][0]
        if candidate.get('finishReason', 'STOP') != 'STOP':
            raise ValueError
        parts = candidate['content']['parts']
        items = [part['inlineData'] for part in parts if isinstance(part, dict) and 'inlineData' in part]
        if len(items) != 1:
            raise ValueError
        item = items[0]
        raw = base64.b64decode(item['data'], validate=True)
        if not raw or len(raw) > MAX_AUDIO:
            raise ValueError
        mime = item.get('mimeType', '').lower()
        if raw.startswith(b'RIFF') and raw[8:12] == b'WAVE':
            encoded = raw
        elif mime.startswith(('audio/l16', 'audio/pcm')):
            rate = re.search(r'(?:^|;)\s*rate\s*=\s*(\d+)', mime)
            channels = re.search(r'(?:^|;)\s*channels\s*=\s*(\d+)', mime)
            if (rate and int(rate.group(1)) != 24000) or (channels and int(channels.group(1)) != 1) or len(raw) % 2:
                raise ValueError
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as out:
                out.setnchannels(1); out.setsampwidth(2); out.setframerate(24000); out.writeframes(raw)
            encoded = buffer.getvalue()
        else:
            raise ValueError
        with wave.open(io.BytesIO(encoded), 'rb') as source:
            rate, count = source.getframerate(), source.getnframes()
            if (source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getcomptype() != 'NONE'
                    or rate <= 0 or count <= 0 or count / rate >= MAX_SECONDS):
                raise ValueError
            frames = source.readframes(count)
            if len(frames) != count * 2 or not any(frames):
                raise ValueError
            return encoded, count / rate
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, wave.Error, EOFError):
        raise gemini_tts.TTSFailure('неполный или некорректный аудиоответ') from None


def request_one(key: str, body: dict, timeout: float):
    request = urllib.request.Request(API, data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json', 'x-goog-api-key': key}, method='POST')
    try:
        with urllib.request.build_opener(gemini_tts.NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise gemini_tts.TTSFailure('слишком большой ответ Gemini')
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise gemini_tts.TTSFailure('некорректный формат ответа Gemini') from None
        return decode_audio(result)
    except urllib.error.HTTPError as exc:
        raise gemini_tts.TTSFailure(f'HTTP {exc.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise gemini_tts.TTSFailure('ошибка связи с Gemini') from None


def prepare(data: dict, folder: Path, ffmpeg: str, keys: list[str]) -> tuple[Path, list[tuple[int, Path]]]:
    """Create one audio per item; a key error never automatically replays a job."""
    bulletin = bulletin_contract.validate_bulletin(data)
    if not keys:
        raise ValueError('Нет ключей Gemini')
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=False, exist_ok=False)
    parts = []
    for index, section in enumerate(bulletin['sections'], 1):
        print(f'Озвучиваю новость {index} из {len(bulletin["sections"])}...', flush=True)
        body = request_body(section)
        raw, normalized = folder / f'{index}-raw.wav', folder / f'{index}-normalized.wav'
        def send(key, _unused, timeout):
            return request_one(key, body, timeout)
        gemini_tts.synthesize('', raw, keys, start_index=index-1, request_fn=send)
        subprocess.run([ffmpeg, '-nostdin', '-y', '-loglevel', 'error', '-i', str(raw),
                        '-ac', '1', '-ar', str(podcast_audio_trial.RATE), '-c:a', 'pcm_s16le', str(normalized)],
                       env=podcast_audio_trial.normalized_env(), check=True)
        rate, samples = podcast_video_trial.read_wav(normalized)
        if rate != podcast_audio_trial.RATE or not any(samples):
            raise ValueError('Пустая или повреждённая новость')
        parts.append((section['id'], normalized))
    dialogue = folder / 'dialogue.wav'
    podcast_audio_trial.assemble_wav([path for _, path in parts], dialogue)
    return dialogue, parts
