"""Validated, bounded worker-side bulletin payload (experimental)."""
from __future__ import annotations

import re
from news_prototype import bulletin_limits, telegram_public_source
from news_prototype.story_audio import validate_script

MAX_TURNS = bulletin_limits.MAX_TURNS
MAX_REMARK_CHARS = 160


def validate_video_remarks(value: object) -> list[dict]:
    """Two short, separately reviewed lines, one per presenter."""
    if (not isinstance(value, list) or len(value) != 2 or
            any(not isinstance(t, dict) or set(t) != {'speaker', 'text'} for t in value)):
        raise ValueError('Нужны две короткие реплики для видео')
    checked = validate_script({'turns': value})['turns']
    if ({t['speaker'] for t in checked} != bulletin_limits.SPEAKERS or
            any(len(t['text']) > 120 or any(ord(c) < 32 or ord(c) == 127 for c in t['text']) for t in checked) or
            sum(len(t['text']) for t in checked) > MAX_REMARK_CHARS):
        raise ValueError('Реплики для видео слишком длинные или неверные')
    return checked


def validate_bulletin(data: object) -> dict:
    if not isinstance(data, dict) or set(data) not in ({'sections'}, {'sections', 'video'}) or not isinstance(data['sections'], list):
        raise ValueError('Неверная задача вестника')
    if not bulletin_limits.MIN_STORIES <= len(data['sections']) <= bulletin_limits.MAX_STORIES:
        raise ValueError('Нужно от двух до двадцати новостей')
    sections, ids, speakers = [], set(), set()
    chars = turns_count = 0
    for section in data['sections']:
        if not isinstance(section, dict) or set(section) not in ({'id', 'ticker', 'turns'}, {'id', 'ticker', 'turns', 'image_b64'}):
            raise ValueError('Неверный раздел')
        number = section.get('id')
        if not bulletin_limits.number(number) or number in ids:
            raise ValueError('Неверный или повторяющийся номер новости')
        ids.add(number)
        if not isinstance(section.get('ticker'), str) or len(section['ticker']) > 120:
            raise ValueError('Неверная строка новости')
        if not isinstance(section.get('turns'), list) or not 1 <= len(section['turns']) <= 8:
            raise ValueError('Неверное число реплик')
        if any(not isinstance(t, dict) or set(t) != {'speaker', 'text'} for t in section['turns']):
            raise ValueError('Неверная реплика')
        checked = validate_script({key: value for key, value in section.items() if key != 'id'})
        if not checked['ticker']:
            raise ValueError('Нужна строка новости')
        speakers.update(turn['speaker'] for turn in checked['turns'])
        turns_count += len(checked['turns'])
        chars += sum(len(turn['text']) for turn in checked['turns'])
        sections.append({'id': int(number), **checked})
    if speakers != bulletin_limits.SPEAKERS or chars > bulletin_limits.MAX_SPOKEN_CHARS or turns_count > MAX_TURNS:
        raise ValueError('Неверный состав или длина вестника')
    result = {'sections': sections}
    if 'video' in data:
        video = data['video']
        fields = {'section_id', 'post_url', 'caption_sha256', 'video_sha256'}
        if (not isinstance(video, dict) or set(video) not in (fields, fields | {'remarks'})
                or not bulletin_limits.number(video.get('section_id')) or video['section_id'] not in ids
                or any(not isinstance(video.get(k), str) or not re.fullmatch(r'[0-9a-f]{64}', video[k])
                       for k in ('caption_sha256', 'video_sha256'))):
            raise ValueError('Неверное утверждённое видео')
        try:
            canonical, _ = telegram_public_source.canonical_post(video['post_url'])
            if canonical != video['post_url']:
                raise ValueError
        except (telegram_public_source.SourceError, ValueError):
            raise ValueError('Неверный источник видео') from None
        result['video'] = dict(video)
        if 'remarks' in video:
            remarks = validate_video_remarks(video['remarks'])
            if chars + sum(len(t['text']) for t in remarks) > bulletin_limits.MAX_SPOKEN_CHARS or turns_count + len(remarks) > MAX_TURNS:
                raise ValueError('Сценарий вместе с репликами к видео слишком длинный')
            result['video']['remarks'] = remarks
    return result
