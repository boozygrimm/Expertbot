"""Attach the owner's specifically uploaded and confirmed video fragment."""
from __future__ import annotations

import copy
import re
from news_prototype import owner_bulletin


async def attach(db, owner_id: int, number: int, revision: str, section_id: int, digest: str) -> str:
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Неверный фрагмент')
    item = await owner_bulletin.get(db, owner_id, number)
    if (not item or item.get('mode') != 'direct' or item.get('stage') != 'ready_for_review'
            or item.get('revision') != revision
            or owner_bulletin.revision(item['stories'], item['sections']) != revision
            or not await owner_bulletin._sources_current(db, owner_id, item)):
        return 'stale'
    sections = copy.deepcopy(item['sections'])
    section = next((s for s in sections if s['id'] == section_id), None)
    if not section or section.get('video'):
        return 'stale'
    video = {'section_id': section_id, 'manual': True, 'video_sha256': digest}
    if section.get('video_remarks'):
        video['remarks'] = section['video_remarks']
    if section.get('video_cut_after'):
        video['cut_after'] = section['video_cut_after']
    section['video'] = video
    owner_bulletin._payload_from_sections(sections)
    updated = owner_bulletin.revision(item['stories'], sections)
    result = await db.news_owner_bulletins.update_one(
        {'_id': owner_bulletin._key(owner_id, number), 'owner_id': owner_id,
         'stage': 'ready_for_review', 'revision': revision},
        {'$set': {'sections': sections, 'revision': updated, 'verification': 'needs_review'}})
    return 'selected' if result.modified_count else 'stale'
