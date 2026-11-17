"""Validated, bounded worker-side bulletin payload (experimental)."""
from __future__ import annotations

from news_prototype import bulletin_limits
from news_prototype.story_audio import validate_script

MAX_TURNS = bulletin_limits.MAX_TURNS


def validate_bulletin(data: object) -> dict:
    if not isinstance(data, dict) or set(data) != {'sections'} or not isinstance(data['sections'], list):
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
    return {'sections': sections}
