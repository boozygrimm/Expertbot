import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from bson import BSON
from bson.int64 import Int64
from news_prototype import bulletin_editor as editor, bulletin_timing as timing


def inputs(count):
    return [{'draft_id': 100000061031 + i, 'url': f'https://example.com/news/{i}',
             'title': f'Новость {i}', 'summary': f'Новость под номером {i} сообщает подробности. ' * 5}
            for i in range(count)]


def response(stories):
    return {'decision': 'draft', 'sections': [
        {'id': int(story['draft_id']), 'ticker': story['title'], 'turns': [
            {'speaker': 'Бендер' if i % 2 == 0 else 'Киберсенсей', 'text': f'Краткая новость номер {i}.',
             'kind': 'fact', 'evidence': story['summary'][:40]}]}
        for i, story in enumerate(stories)]}


class BulletinEditorTests(unittest.TestCase):
    def test_prompt_distinguishes_bender_jokes_from_facts(self):
        prompt, _ = editor.prompt_for(inputs(2))
        self.assertIn('в шутках — нагло, ехидно', prompt)
        self.assertIn('факты сообщает ясно, без насмешки', prompt)
        self.assertIn('если шутка неуместна, обойдись без неё', prompt)
        self.assertIn('шутки и предположения не выдавай за факты', prompt)
        self.assertIn('Киберсенсей говорит тепло и живо', prompt)
        self.assertIn('интерес, удивление или сочувствие', prompt)
        self.assertIn('смарт-контракт не обязательно приложение', prompt)
        self.assertIn('информация остаётся', prompt.lower())
        self.assertIn('Не называй фактическое', prompt)

    def test_twenty_selected_news_keep_order_and_use_both_presenters(self):
        stories = inputs(20)
        # Simulate actual MongoDB round trip for batch draft IDs.
        stories = BSON(BSON.encode({'stories': stories})).decode()['stories']
        self.assertIs(type(stories[0]['draft_id']), Int64)
        prompt, material = editor.prompt_for(stories)
        self.assertIn('не выполняй', prompt.lower())
        self.assertEqual(len(json.loads(material)['stories']), 20)
        result = editor.parse_draft(json.dumps(response(stories), ensure_ascii=False), stories)
        self.assertEqual([s['id'] for s in result['sections']], [int(s['draft_id']) for s in stories])
        self.assertEqual(result['verification'], 'needs_review')
        self.assertEqual({s['turns'][0]['speaker'] for s in result['sections']}, editor.SPEAKERS)

    def test_missing_reordered_duplicate_or_cross_story_evidence_rejected(self):
        stories = inputs(3)
        for mutation in ('missing', 'reordered', 'duplicate', 'wrong_quote', 'no_fact', 'one_voice'):
            data = response(stories)
            sections = data['sections']
            if mutation == 'missing': sections.pop()
            elif mutation == 'reordered': sections.reverse()
            elif mutation == 'duplicate': sections[-1]['id'] = sections[0]['id']
            elif mutation == 'wrong_quote': sections[0]['turns'][0]['evidence'] = stories[1]['summary'][:40]
            elif mutation == 'no_fact': sections[0]['turns'][0].update(kind='comment', evidence='')
            elif mutation == 'one_voice':
                for section in sections: section['turns'][0]['speaker'] = 'Бендер'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                editor.parse_draft(json.dumps(data, ensure_ascii=False), stories)

    def test_limits_and_untrusted_model_values(self):
        stories = inputs(2)
        for bad in (inputs(1), inputs(21), inputs(2) + inputs(1)):
            with self.assertRaises(ValueError): editor.material_for(bad)
        for bad_url in ('http://example.com', 'https://127.0.0.1/x'):
            copy = inputs(2); copy[0]['url'] = bad_url
            with self.assertRaises(ValueError): editor.material_for(copy)
        data = response(stories)
        data['sections'][0]['turns'] = [data['sections'][0]['turns'][0] | {'text': 'a' * 470} for _ in range(7)]
        with self.assertRaises(ValueError): editor.parse_draft(json.dumps(data), stories)
        data = response(stories); data['sections'][0]['turns'][0]['speaker'] = []
        with self.assertRaises(ValueError): editor.parse_draft(json.dumps(data), stories)
        for ids in ([{}], [stories[0]['draft_id'], stories[0]['draft_id']], [999]):
            with self.assertRaises(ValueError):
                editor.parse_draft(json.dumps({'decision': 'insufficient', 'ids': ids}), stories)
        result = editor.parse_draft(json.dumps({'decision': 'insufficient', 'ids': [stories[0]['draft_id']]}), stories)
        self.assertEqual(result['decision'], 'insufficient')

    def test_editor_never_accepts_more_turns_than_worker_contract(self):
        stories = inputs(20); data = response(stories)
        for section in data['sections']:
            section['turns'] = [dict(section['turns'][0], text='Кратко.') for _ in range(6)]
        with self.assertRaisesRegex(ValueError, 'много реплик'):
            editor.parse_draft(json.dumps(data, ensure_ascii=False), stories)
        for section in data['sections']:
            section['turns'].pop()
        result = editor.parse_draft(json.dumps(data, ensure_ascii=False), stories)
        from news_prototype.owner_bulletin import _payload_from_sections
        self.assertEqual(len(_payload_from_sections(result['sections'])['sections']), 20)

    def test_each_generated_section_fits_a_telegram_edit_message(self):
        stories = inputs(2)
        data = response(stories)
        turn = data['sections'][0]['turns'][0]
        data['sections'][0]['turns'] = [dict(turn, text='😀' * 470) for _ in range(5)]
        with self.assertRaisesRegex(ValueError, 'Telegram'):
            editor.parse_draft(json.dumps(data, ensure_ascii=False), stories)

    def test_model_cannot_replace_trusted_source_urls(self):
        stories = inputs(2); data = response(stories)
        data['sections'][0]['url'] = 'https://evil.example/another'
        result = editor.parse_draft(json.dumps(data), stories)
        self.assertEqual(result['sections'][0]['url'], stories[0]['url'])


class BulletinTimingTests(unittest.TestCase):
    def test_uses_actual_pcm_durations_and_inter_turn_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            parts = []
            for number, seconds in enumerate((1, 2, 3)):
                path = Path(folder) / f'{number}.wav'
                with wave.open(str(path), 'wb') as output:
                    output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000)
                    output.writeframes(bytes(2 * 24000 * seconds))
                parts.append((10 if number < 2 else 11, path))
            windows, total = timing.section_windows(parts)
            self.assertAlmostEqual(windows[0].start, 1.75)
            self.assertAlmostEqual(windows[0].end, 1.75 + 3 + .35)
            self.assertAlmostEqual(windows[1].start, 1.75 + 3 + .7)
            self.assertGreater(total, windows[1].end)
            self.assertLess(total, 11)

    def test_exact_five_minute_boundary_counts_whole_output_frames(self):
        from news_prototype import combined_trial, podcast_audio_trial
        rate = podcast_audio_trial.RATE
        fps = combined_trial.bender.FPS
        dialogue_frames = 300 * fps - combined_trial.INTRO_FRAMES - combined_trial.TAIL_FRAMES
        samples = dialogue_frames * rate // fps - 5 * round(rate * podcast_audio_trial.GAP_MS / 1000)
        sizes = [samples // 6] * 6
        sizes[-1] += samples - sum(sizes)
        self.assertTrue(all(0 < n < rate * 60 for n in sizes))
        parts = [(i + 1, Path(str(i))) for i in range(6)]
        def read(path): return rate, range(sizes[int(path.name)])
        with patch.object(timing.podcast_video_trial, 'read_wav', side_effect=read):
            _, total = timing.section_windows(parts)
            self.assertEqual(total, 300)
            sizes[-1] += 1
            with self.assertRaises(timing.BulletinTooLong):
                timing.section_windows(parts)

    def test_actual_length_cap_no_truncation_and_repeated_section_rejected(self):
        with patch.object(timing.podcast_video_trial, 'read_wav', return_value=(24000, range(24000 * 149))):
            parts = [(1, Path('a')), (2, Path('b'))]
            with self.assertRaises(timing.BulletinTooLong): timing.section_windows(parts)
            with self.assertRaises(ValueError): timing.section_windows(parts + [(1, Path('c'))])
        with patch.object(timing.podcast_video_trial, 'read_wav', return_value=(24000, range(24000))):
            windows, total = timing.section_windows([(1, Path('a')), (2, Path('b'))], max_seconds=300)
            self.assertLess(total, 300)
            self.assertEqual(len(windows), 2)

class BulletinClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_model_request_validates_all_sections_without_render(self):
        from news_prototype import editor_client
        from tests.test_editor_client import Response, Session
        stories = inputs(4)
        output = response(stories)
        raw = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(output)}]}}]}
        calls = []
        session = Session(Response(200, json.dumps(raw).encode()), calls)
        with patch.object(editor_client.aiohttp, 'ClientSession', return_value=session):
            result = await editor_client.bulletin(stories, 'not-real-key', 'gemini-test')
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1]['allow_redirects'])
        self.assertEqual(len(result['sections']), 4)
        self.assertNotIn('not-real-key', calls[0][0])

    async def test_rejects_missing_sections_without_retry(self):
        from news_prototype import editor_client
        from tests.test_editor_client import Response, Session
        stories = inputs(4); output = response(stories); output['sections'].pop()
        raw = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(output)}]}}]}
        calls = []
        with patch.object(editor_client.aiohttp, 'ClientSession', return_value=Session(Response(200, json.dumps(raw).encode()), calls)):
            with self.assertRaises(editor_client.EditorialError):
                await editor_client.bulletin(stories, 'not-real-key', 'gemini-test')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__': unittest.main()
