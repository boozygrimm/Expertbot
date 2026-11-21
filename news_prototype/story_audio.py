"""Explicit, isolated Gemini TTS preparation for a reviewed local story script.

Not a scheduler or bot worker. Running this spends existing Gemini quota. The
output directory must not exist; completed turns and script are retained on
failure. There is no automatic retry of the whole story or Telegram delivery.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

from news_prototype import podcast_audio_trial as podcast
from news_prototype import podcast_video_trial as video
from news_prototype.run_bender_trial import load_keys
from video_prototype import gemini_tts

VOICES = {'Бендер': ('Algenib', podcast.BENDER_STYLE),
          'Киберсенсей': ('Iapetus', podcast.SENSEI_STYLE)}


def validate_script(data: object) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get('turns'), list) or not data['turns']:
        raise ValueError('В сценарии нужны реплики')
    if len(data['turns']) > 100:
        raise ValueError('Слишком много реплик')
    result = []
    for turn in data['turns']:
        if not isinstance(turn, dict):
            raise ValueError('Неверная реплика')
        who, text = turn.get('speaker'), turn.get('text')
        if not isinstance(who, str) or who not in VOICES or not isinstance(text, str):
            raise ValueError('Неверный ведущий или текст реплики')
        text = text.strip()
        # An individual existing Gemini TTS task is bounded; never truncate.
        if not text or len(text) > 480 or any(ord(c) < 32 and c not in '\n\t' for c in text):
            raise ValueError('Пустая, повреждённая или слишком длинная реплика')
        result.append({'speaker': who, 'text': text})
    ticker = data.get('ticker')
    if ticker is not None and (not isinstance(ticker, str) or not ticker.strip() or len(ticker) > 200):
        raise ValueError('Неверный текст строки')
    script = {'turns': result, 'ticker': ticker}
    if 'image_b64' in data:
        # Strictly validate the bounded, sanitized JPEG at every trust boundary.
        from news_prototype.news_image_codec import decode_thumbnail
        decode_thumbnail(data['image_b64'])
        script['image_b64'] = data['image_b64']
    if 'image_regions' in data:
        if 'image_b64' not in script:
            raise ValueError('Области без изображения')
        from news_prototype.image_focus import validate_regions
        script['image_regions'] = validate_regions(data['image_regions'])
    if 'video_sha256' in data:
        # Only the digest of an already uploaded, worker-verified fragment is
        # accepted here; the bytes never travel inside a script.
        digest = data['video_sha256']
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('Неверный отпечаток видеовставки')
        script['video_sha256'] = digest
    return script


def prepare(script: dict, folder: Path, ffmpeg: str, keys: list[str]) -> Path:
    """Create new private work directory and leave all results on failure."""
    script = validate_script(script)
    if not keys:
        raise ValueError('Нет ключей Gemini')
    folder.mkdir(mode=0o700, parents=False, exist_ok=False)
    (folder / 'script.json').write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding='utf-8')
    files = []
    old_voice, old_style = gemini_tts.VOICE, gemini_tts.STYLE
    try:
        for index, turn in enumerate(script['turns'], start=1):
            raw = folder / f'{index}-raw.wav'
            normalized = folder / f'{index}-normalized.wav'
            gemini_tts.VOICE, gemini_tts.STYLE = VOICES[turn['speaker']]
            print(f'Озвучиваю реплику {index} из {len(script["turns"])}...', flush=True)
            gemini_tts.synthesize(turn['text'], raw, keys, start_index=index-1)
            subprocess.run([ffmpeg, '-nostdin', '-y', '-loglevel', 'error', '-i', str(raw),
                            '-ac', '1', '-ar', str(podcast.RATE), '-c:a', 'pcm_s16le', str(normalized)],
                           env=podcast.normalized_env(), check=True)
            rate, samples = video.read_wav(normalized)
            if not any(samples) or len(samples) / rate >= 60:
                raise ValueError('Пустая, тихая или слишком длинная реплика')
            files.append(normalized)
    finally:
        gemini_tts.VOICE, gemini_tts.STYLE = old_voice, old_style
    combined = folder / 'dialogue.wav'
    podcast.assemble_wav(files, combined)
    turns = [{'speaker': turn['speaker'], 'audio': path.name} for turn, path in zip(script['turns'], files)]
    video.dialogue_timeline_from_parts(combined, [(path, turn['speaker']) for turn, path in zip(script['turns'], files)])
    manifest = {'turns': turns, 'ticker': script['ticker']}
    temp = folder / 'story.json.tmp'
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(folder / 'story.json')
    return folder / 'story.json'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', type=Path, required=True, help='JSON сценарий с ролями и текстами')
    parser.add_argument('--output-dir', type=Path, required=True, help='Новая папка, ещё не существующая')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    if args.script.stat().st_size > 100_000:
        raise SystemExit('Сценарий слишком большой')
    script = json.loads(args.script.read_text(encoding='utf-8'))
    print('Запрашиваю озвучку Gemini: используется существующая квота.', flush=True)
    path = prepare(script, args.output_dir, args.ffmpeg, load_keys())
    print(f'Аудио готово; манифест: {path}', flush=True)


if __name__ == '__main__': main()
