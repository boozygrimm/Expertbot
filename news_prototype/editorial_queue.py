"""Explicit single-step editorial processor, not scheduled or enabled by default.

A claimed candidate is not re-claimed automatically after a crash. Drafts are
persisted before idempotent rendering admission; source claims still require
human review before any publication.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pymongo import ReturnDocument
from news_prototype import news_queue, story_editor


async def run_once(candidates, render_jobs, draft, fetch_article=None) -> str:
    # Persisted draft is handled first, so a crash after draft creation can
    # resume admission without re-generating the script.
    item = await candidates.find_one({'status': 'draft_ready'}, sort=[('collected_at', 1)])
    if item:
        try:
            await news_queue.enqueue_script(render_jobs, str(item['_id']), item['draft']['script'])
        except (ValueError, KeyError, TypeError):
            await candidates.update_one({'_id': item['_id'], 'status': 'draft_ready'},
                                        {'$set': {'status': 'editor_failed'}})
            return 'editor_failed'
        await candidates.update_one({'_id': item['_id'], 'status': 'draft_ready'},
                                    {'$set': {'status': 'render_queued'}})
        return 'render_queued'
    item = await candidates.find_one_and_update(
        {'status': 'candidate'}, {'$set': {'status': 'editing', 'editing_started_at': datetime.now(timezone.utc)}},
        sort=[('collected_at', 1)], return_document=ReturnDocument.AFTER)
    if not item:
        return 'idle'
    try:
        story_editor.prompt_for(item)
    except ValueError:
        if not fetch_article or item.get('source_id') != 'manual' or not item.get('url'):
            await candidates.update_one({'_id': item['_id'], 'status': 'editing'}, {'$set': {'status': 'needs_source'}})
            return 'needs_source'
        try:
            article = await fetch_article(item['url'])
            if not isinstance(article, dict) or not isinstance(article.get('summary'), str):
                raise ValueError('Неверный текст источника')
            additions = {key: article[key] for key in ('title', 'summary', 'resolved_url') if key in article}
            item = item | additions
            story_editor.prompt_for(item)
            # Keep exactly the source material used to draft and review the story.
            saved = await candidates.update_one({'_id': item['_id'], 'status': 'editing'}, {'$set': additions})
            if not saved.matched_count:
                return 'cancelled'
        except Exception:
            await candidates.update_one({'_id': item['_id'], 'status': 'editing'}, {'$set': {'status': 'needs_source'}})
            return 'needs_source'
    try:
        result = await draft(item)
        if not isinstance(result, dict) or result.get('decision') not in ('skip', 'draft'):
            raise ValueError('Неверное решение редактора')
        if result['decision'] == 'skip':
            await candidates.update_one({'_id': item['_id'], 'status': 'editing'}, {'$set': {'status': 'skipped'}})
            return 'skipped'
        if result.get('verification') != 'needs_review' or not isinstance(result.get('script'), dict):
            raise ValueError('Неверный сценарий')
        # Validate before persistence; keep the supplied evidence for review.
        from news_prototype.story_audio import validate_script
        validate_script(result['script'])
        await candidates.update_one({'_id': item['_id'], 'status': 'editing'},
                                    {'$set': {'status': 'draft_ready', 'draft': result,
                                              'verification': 'needs_review'}})
        return 'draft_ready'
    except Exception:
        # No exception message: provider errors may contain input or credentials.
        await candidates.update_one({'_id': item['_id'], 'status': 'editing'}, {'$set': {'status': 'editor_failed'}})
        return 'editor_failed'
