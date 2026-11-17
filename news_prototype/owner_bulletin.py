"""Owner bulletin preparation and snapshots, without rendering or dispatch.

A separate durable document records one bounded, explicit model request. It is
not allowed to enqueue, synthesize, render, send or publish anything.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from news_prototype import bulletin_contract, bulletin_editor, bulletin_limits, owner_drafts, owner_program


def _key(owner_id: int, number: int) -> str:
    if not bulletin_editor._number(owner_id) or not bulletin_editor._number(number):
        raise ValueError('Неверный идентификатор вестника')
    return f'owner-bulletin:{owner_id}:{number}'


def _snapshot(item: dict) -> dict:
    script = item.get('script') or {}
    snapshot = {'draft_id': int(item['draft_id']), 'url': item['url'], 'title': item.get('title') or '',
                'summary': item.get('summary') or item.get('text') or '', 'revision': item.get('revision')}
    if 'image_b64' in script:
        snapshot['image_b64'] = script['image_b64']
    if item.get('image_kind'):
        snapshot['image_kind'] = item['image_kind']
    return snapshot


def revision(stories: list[dict], sections: list[dict]) -> str:
    value = json.dumps({'stories': stories, 'sections': sections}, sort_keys=True,
                       ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(value).hexdigest()


async def create(db, owner_id: int, number: int, draft_ids: list[int]) -> bool:
    key = _key(owner_id, number)
    if (not isinstance(draft_ids, list) or not bulletin_editor.MIN_STORIES <= len(draft_ids) <= bulletin_editor.MAX_STORIES
            or any(not bulletin_editor._number(x) for x in draft_ids) or len(set(draft_ids)) != len(draft_ids)):
        raise ValueError('Нужны от двух до двадцати разных новостей')
    stories = []
    for draft_id in draft_ids:
        item = await owner_drafts.get(db, owner_id, draft_id)
        if not await owner_program.eligible_draft(db, owner_id, item):
            raise ValueError('Новость не готова к проверке')
        if item.get('revision') != owner_drafts.revision(item['url'], item['script']):
            raise ValueError('Версия новости не совпадает')
        stories.append(_snapshot(item))
    bulletin_editor.material_for(stories)
    result = await db.news_owner_bulletins.update_one({'_id': key}, {'$setOnInsert': {
        'owner_id': owner_id, 'bulletin_id': int(number), 'stories': stories, 'stage': 'new',
        'created_at': datetime.now(timezone.utc)}}, upsert=True)
    if result.upserted_id is not None:
        return True
    current = await db.news_owner_bulletins.find_one({'_id': key})
    if not current or current.get('owner_id') != owner_id or current.get('stories') != stories:
        raise ValueError('Номер уже используется')
    return False


async def get(db, owner_id: int, number: int) -> dict | None:
    return await db.news_owner_bulletins.find_one({'_id': _key(owner_id, number), 'owner_id': owner_id})


async def _sources_current(db, owner_id: int, item: dict) -> bool:
    stories = item['stories']
    if item.get('mode') == 'direct':
        try:
            choices = item['choices']
            if len(stories) != len(choices) or any(
                    story['draft_id'] != choice['draft_id'] or story['url'] != choice['url']
                    for story, choice in zip(stories, choices)):
                return False
            bulletin_editor.material_for(stories)
            return item.get('source_revision') == revision(stories, [])
        except (KeyError, TypeError, ValueError):
            return False
    for source in stories:
        current = await owner_drafts.get(db, owner_id, source['draft_id'])
        if not await owner_program.eligible_draft(db, owner_id, current) or _snapshot(current) != source:
            return False
        if current.get('revision') != owner_drafts.revision(current['url'], current['script']):
            return False
    return True


async def prepare(db, owner_id: int, number: int, editor) -> str:
    """One claimed external request; ambiguous/failed results are not retried."""
    key = _key(owner_id, number)
    item = await get(db, owner_id, number)
    if not item or item.get('stage') != 'new':
        return 'not_new'
    if not await _sources_current(db, owner_id, item):
        return 'stale'
    claimed = await db.news_owner_bulletins.update_one({'_id': key, 'stage': 'new', 'owner_id': owner_id},
        {'$set': {'stage': 'editing'}})
    if not claimed.modified_count:
        return 'not_new'
    try:
        generated = await editor(item['stories'])
        # Validate every boundary even when the editor callback is injected.
        # Editor returns the already-validated result, but reconstructing the
        # response guarantees source/ordering checks here as well.
        if not isinstance(generated, dict):
            raise ValueError('Неверный результат редактора')
        if generated.get('decision') == 'insufficient':
            safe = bulletin_editor.parse_draft(json.dumps({'decision': 'insufficient', 'ids': generated.get('ids')}), item['stories'])
            await db.news_owner_bulletins.update_one({'_id': key, 'stage': 'editing'},
                {'$set': {'stage': 'needs_source', 'missing_ids': safe['ids']}})
            return 'needs_source'
        sections = generated.get('sections')
        safe = bulletin_editor.parse_draft(json.dumps({'decision': generated.get('decision'), 'sections': sections}, ensure_ascii=False), item['stories'])
        sections = safe['sections']
        for section, source in zip(sections, item['stories']):
            if 'image_b64' in source:
                section['image_b64'] = source['image_b64']
        result = await db.news_owner_bulletins.update_one({'_id': key, 'stage': 'editing'}, {'$set': {
            'stage': 'ready_for_review', 'sections': sections,
            'revision': revision(item['stories'], sections), 'verification': 'needs_review'}})
        return 'ready_for_review' if result.modified_count else 'changed'
    except Exception:
        await db.news_owner_bulletins.update_one({'_id': key, 'stage': 'editing'}, {'$set': {'stage': 'editor_failed'}})
        return 'editor_failed'


async def approved_payload(db, owner_id: int, number: int, expected: str) -> dict:
    """Validate an owner's exact reviewed version; does not start any work."""
    item = await get(db, owner_id, number)
    if (not item or item.get('stage') != 'ready_for_review' or item.get('revision') != expected
            or revision(item['stories'], item['sections']) != expected):
        raise ValueError('Версия вестника изменилась')
    if not await _sources_current(db, owner_id, item):
        raise ValueError('Один из источников изменился')
    return _payload_from_sections(item['sections'])


def _payload_from_sections(sections: list[dict]) -> dict:
    """Validate the final render contract without treating owner edits as facts."""
    result = []
    for section in sections:
        clean = {'id': section['id'], 'ticker': section['ticker'],
                 'turns': [{'speaker': turn['speaker'], 'text': turn['text']} for turn in section['turns']]}
        if 'image_b64' in section:
            clean['image_b64'] = section['image_b64']
        result.append(clean)
    videos = [section['video'] for section in sections if 'video' in section]
    if len(videos) > 1:
        raise ValueError('В выпуске поддерживается одно видео')
    payload = {'sections': result}
    if videos:
        if not isinstance(videos[0], dict):
            raise ValueError('Неверное видео')
        payload['video'] = videos[0]
        selected = next((section for section in sections if section['id'] == videos[0].get('section_id')
                         and 'video' in section), None)
        if selected is None:
            raise ValueError('Видео привязано к другой новости')
        if videos[0].get('remarks') != selected.get('video_remarks'):
            raise ValueError('Реплики видео не совпадают с проверенным сценарием')
    return bulletin_contract.validate_bulletin(payload)


def parse_section_edit(text: str) -> dict:
    """One headline and 1–8 single-line turns; no URLs or images from input."""
    if not isinstance(text, str) or len(text.encode('utf-16-le')) // 2 > 3800:
        raise ValueError('Слишком длинное сообщение')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError('Нужны заголовок и реплики')
    field, sep, title = lines[0].partition(':')
    if not sep or field.casefold() != 'строка' or not title.strip() or len(title.strip()) > 120:
        raise ValueError('Первая строка: Строка: заголовок')
    turns = []
    for line in lines[1:]:
        speaker, sep, content = line.partition(':')
        if not sep or speaker not in bulletin_editor.SPEAKERS or not content.strip():
            raise ValueError('Каждая реплика начинается с Бендер: или Киберсенсей:')
        turns.append({'speaker': speaker, 'text': content.strip()})
    if not 1 <= len(turns) <= 8:
        raise ValueError('Нужно от одной до восьми реплик')
    return {'ticker': title.strip(), 'turns': turns}


def parse_video_remarks(text: str) -> list[dict] | None:
    """Parse two reviewed overlay comments; a single dash removes them."""
    if not isinstance(text, str) or len(text) > 500:
        raise ValueError('Нужны две короткие реплики')
    if text.strip() == '-':
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    turns = []
    for line in lines:
        speaker, sep, content = line.partition(':')
        if not sep:
            raise ValueError('Начни каждую реплику с имени ведущего и двоеточия')
        turns.append({'speaker': speaker.strip(), 'text': content.strip()})
    return bulletin_contract.validate_video_remarks(turns)


async def change_video_remarks(db, owner_id: int, number: int, expected: str, section_id: int, text: str) -> str:
    """Save only one pair of owner-reviewed comments, invalidating media approval."""
    import copy
    remarks = parse_video_remarks(text)
    item = await get(db, owner_id, number)
    if (not item or item.get('mode') != 'direct' or item.get('stage') != 'ready_for_review'
            or item.get('revision') != expected or revision(item['stories'], item['sections']) != expected
            or not await _sources_current(db, owner_id, item)):
        return 'stale'
    story = next((s for s in item['stories'] if s['draft_id'] == section_id), None)
    sections = copy.deepcopy(item['sections'])
    section = next((s for s in sections if s['id'] == section_id), None)
    if not section or not story or story.get('telegram_video_status') != 'available':
        return 'stale'
    if section.get('video_remarks') == remarks and not any(s.get('video') for s in sections):
        return 'unchanged'
    for part in sections:
        part.pop('video', None)
        part.pop('video_remarks', None)
    if remarks is not None:
        section['video_remarks'] = remarks
    # Enforce the full text budget even before media is selected.
    payload = _payload_from_sections(sections)
    spoken = sum(len(turn['text']) for s in payload['sections'] for turn in s['turns'])
    count = sum(len(s['turns']) for s in payload['sections'])
    if remarks and (spoken + sum(len(t['text']) for t in remarks) > bulletin_limits.MAX_SPOKEN_CHARS
                    or count + len(remarks) > bulletin_contract.MAX_TURNS):
        raise ValueError('Выпуск с этими репликами слишком длинный')
    new_rev = revision(item['stories'], sections)
    if new_rev == expected:
        return 'unchanged'
    changed = await db.news_owner_bulletins.update_one({'_id': _key(owner_id, number), 'owner_id': owner_id,
        'stage': 'ready_for_review', 'revision': expected}, {'$set': {
            'sections': sections, 'revision': new_rev, 'verification': 'needs_review'}})
    return 'saved' if changed.modified_count else 'stale'


def editable_section(section: dict) -> str:
    lines = ['Строка: ' + section['ticker']]
    lines.extend(turn['speaker'] + ': ' + ' '.join(turn['text'].splitlines()) for turn in section['turns'])
    text = '\n'.join(lines)
    if len(text.encode('utf-16-le')) // 2 > 3800:
        raise ValueError('Раздел слишком длинный для Telegram')
    return text


async def change_section(db, owner_id: int, number: int, expected: str, section_id: int, *,
                         text: str | None = None, remove_image: bool = False) -> str:
    """CAS edit only the reviewed revision, never touch the source draft or queue."""
    import copy
    if not owner_drafts.REVISION.fullmatch(expected or '') or (text is None) == (not remove_image):
        raise ValueError('Неверное изменение')
    item = await get(db, owner_id, number)
    if (not item or item.get('stage') != 'ready_for_review' or item.get('revision') != expected or
            revision(item['stories'], item['sections']) != expected):
        return 'stale'
    if not await _sources_current(db, owner_id, item):
        return 'stale_source'
    sections = copy.deepcopy(item['sections'])
    # A media approval is specific to the entire reviewed version. Any edit
    # invalidates it; the owner must inspect/approve the new version again.
    for part in sections:
        part.pop('video', None)
    section = next((part for part in sections if part['id'] == section_id), None)
    if section is None:
        raise ValueError('Новость не найдена')
    if remove_image:
        section.pop('image_b64', None)
    else:
        edited = parse_section_edit(text)
        old_turns = section['turns']
        checked = []
        for index, turn in enumerate(edited['turns']):
            old = old_turns[index] if index < len(old_turns) else None
            if old and all(old[k] == turn[k] for k in ('speaker', 'text')):
                checked.append(old)
            else:
                checked.append({**turn, 'kind': 'owner_edit', 'evidence': ''})
        section['ticker'], section['turns'] = edited['ticker'], checked
    _payload_from_sections(sections)
    new_rev = revision(item['stories'], sections)
    if new_rev == expected:
        return 'unchanged'
    result = await db.news_owner_bulletins.update_one(
        {'_id': _key(owner_id, number), 'owner_id': owner_id, 'stage': 'ready_for_review', 'revision': expected},
        {'$set': {'sections': sections, 'revision': new_rev, 'verification': 'needs_review'}})
    return 'saved' if result.modified_count else 'stale'
