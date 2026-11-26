"""`/song`: поиск, выбор трека и выдача файла. Сеть и yt-dlp подменены."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from handlers import music


TRACKS = [
    {'title': 'Ночной <эфир>', 'performer': 'Группа & Ко', 'url': 'https://sc.test/1', 'duration': 245},
    {'title': 'Второй', 'performer': 'Кто-то', 'url': 'https://sc.test/2', 'duration': None},
]


def message(text='/song тест', user_id=7, reply=None):
    return type('Message', (), {
        'text': text,
        'from_user': type('User', (), {'id': user_id})(),
        'reply': reply or AsyncMock(),
        'answer_audio': AsyncMock(),
        'delete': AsyncMock(),
    })()


class FormatDurationTests(unittest.TestCase):
    def test_minutes_and_padded_seconds(self):
        self.assertEqual(music.format_duration(245), ' (4:05)')
        self.assertEqual(music.format_duration(60), ' (1:00)')
        self.assertEqual(music.format_duration(59), ' (0:59)')

    def test_missing_or_nonsense_duration_is_silent(self):
        for value in (None, 0, -5):
            with self.subTest(value=value):
                self.assertEqual(music.format_duration(value), '')


class SearchTests(unittest.TestCase):
    def test_search_asks_soundcloud_for_five_flat_entries(self):
        ydl = MagicMock()
        ydl.__enter__ = MagicMock(return_value=ydl)
        ydl.__exit__ = MagicMock(return_value=False)
        ydl.extract_info.return_value = {'entries': [
            {'title': 'A', 'uploader': 'U', 'url': 'https://sc.test/a', 'duration': 10},
            None,
            {'title': 'B', 'uploader': 'V', 'webpage_url': 'https://sc.test/b'},
        ]}
        with patch.object(music.yt_dlp, 'YoutubeDL', return_value=ydl) as ctor:
            found = music.search_track_candidates_sync('кино')
        self.assertEqual(ctor.call_args.args[0], {'extract_flat': True, 'quiet': True, 'noplaylist': True})
        self.assertEqual(ydl.extract_info.call_args.args[0], 'scsearch5:кино')
        self.assertEqual([c['title'] for c in found], ['A', 'B'], 'пустые записи пропускаются')
        self.assertEqual(found[1]['url'], 'https://sc.test/b', 'url берётся из webpage_url как запасной')
        self.assertIsNone(found[1]['duration'])

    def test_search_failure_is_not_an_exception_for_the_caller(self):
        with patch.object(music.yt_dlp, 'YoutubeDL', side_effect=RuntimeError('сеть')):
            self.assertEqual(music.search_track_candidates_sync('кино'), [])

    def test_response_without_entries_yields_nothing(self):
        ydl = MagicMock()
        ydl.__enter__ = MagicMock(return_value=ydl)
        ydl.__exit__ = MagicMock(return_value=False)
        ydl.extract_info.return_value = {'title': 'плейлист'}
        with patch.object(music.yt_dlp, 'YoutubeDL', return_value=ydl):
            self.assertEqual(music.search_track_candidates_sync('кино'), [])


class DownloadTests(unittest.TestCase):
    def test_download_uses_best_audio_in_temp_dir(self):
        ydl = MagicMock()
        ydl.__enter__ = MagicMock(return_value=ydl)
        ydl.__exit__ = MagicMock(return_value=False)
        ydl.prepare_filename.return_value = '/tmp/x.webm'
        with patch.object(music.yt_dlp, 'YoutubeDL', return_value=ydl) as ctor:
            path = music.download_audio_raw_sync('https://sc.test/1')
        opts = ctor.call_args.args[0]
        self.assertEqual(opts['format'], 'bestaudio/best')
        self.assertEqual(opts['retries'], 3)
        self.assertTrue(opts['outtmpl'].startswith(os.path.join(os.path.dirname(opts['outtmpl']), '')))
        self.assertIn('%(ext)s', opts['outtmpl'], 'расширение определяет yt-dlp, а не код')
        self.assertEqual(path, '/tmp/x.webm')

    def test_download_failure_returns_none(self):
        with patch.object(music.yt_dlp, 'YoutubeDL', side_effect=RuntimeError('сеть')):
            self.assertIsNone(music.download_audio_raw_sync('https://sc.test/1'))


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        music.music_pick_sessions.clear()
        self.addCleanup(music.music_pick_sessions.clear)

    async def test_empty_query_asks_for_a_name(self):
        msg = message('/song')
        await music.handle_music_request(msg, '   ')
        msg.reply.assert_awaited_once()
        self.assertIn('название', msg.reply.await_args.args[0])

    async def test_results_escape_names_and_carry_duration(self):
        status = AsyncMock()
        msg = message()
        msg.reply = AsyncMock(return_value=status)
        with patch.object(music, 'normalize_music_query', AsyncMock(return_value='кино')), \
             patch.object(music, 'search_track_candidates_sync', return_value=TRACKS):
            await music.handle_music_request(msg, 'кино')
        text = status.edit_text.await_args.args[0]
        self.assertIn('Ночной &lt;эфир&gt;', text)
        self.assertIn('Группа &amp; Ко', text)
        self.assertIn('(4:05)', text)
        self.assertEqual(status.edit_text.await_args.kwargs['parse_mode'], 'HTML')
        session = next(iter(music.music_pick_sessions.values()))
        self.assertEqual(session['user_id'], 7)
        self.assertEqual(len(session['candidates']), 2)

    async def test_failed_normalisation_falls_back_to_the_raw_query(self):
        status = AsyncMock()
        msg = message()
        msg.reply = AsyncMock(return_value=status)
        with patch.object(music, 'normalize_music_query', AsyncMock(side_effect=RuntimeError)), \
             patch.object(music, 'search_track_candidates_sync', return_value=TRACKS) as search:
            await music.handle_music_request(msg, '  кино  ')
        self.assertEqual(search.call_args.args[0], '  кино  ')

    async def test_no_results_are_reported_on_the_status_message(self):
        status = AsyncMock()
        msg = message()
        msg.reply = AsyncMock(return_value=status)
        with patch.object(music, 'normalize_music_query', AsyncMock(return_value='кино')), \
             patch.object(music, 'search_track_candidates_sync', return_value=[]):
            await music.handle_music_request(msg, 'кино')
        self.assertIn('Ничего не нашел', status.edit_text.await_args.args[0])
        self.assertEqual(music.music_pick_sessions, {}, 'пустой поиск не создаёт сессию')


class PickTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        music.music_pick_sessions.clear()
        self.addCleanup(music.music_pick_sessions.clear)

    def session(self, **over):
        data = {'user_id': 7, 'candidates': TRACKS, **over}
        music.music_pick_sessions['abc12345'] = data
        return data

    def callback(self, data='snd:abc12345:0', user_id=7):
        return type('Callback', (), {
            'data': data,
            'from_user': type('User', (), {'id': user_id})(),
            'answer': AsyncMock(),
            'message': type('Message', (), {'edit_text': AsyncMock(), 'answer_audio': AsyncMock(),
                                            'delete': AsyncMock()})(),
        })()

    async def test_foreign_click_does_not_touch_the_session(self):
        self.session()
        cb = self.callback(user_id=8)
        await music.music_callback(cb)
        self.assertIn('сессия истекла', cb.answer.await_args.args[0])
        self.assertIn('abc12345', music.music_pick_sessions, 'чужая попытка не съедает сессию')
        cb.message.answer_audio.assert_not_awaited()

    async def test_expired_session_is_reported(self):
        cb = self.callback(data='snd:gone:0')
        await music.music_callback(cb)
        self.assertIn('сессия истекла', cb.answer.await_args.args[0])

    async def test_successful_pick_sends_audio_and_removes_temp_file(self):
        self.session()
        cb = self.callback()
        with patch.object(music, 'download_audio_raw_sync', return_value='/tmp/track.webm'), \
             patch.object(music.os.path, 'exists', return_value=True), \
             patch.object(music.os, 'remove') as remove:
            await music.music_callback(cb)
        self.assertIn('Загружаю', cb.message.edit_text.await_args.args[0])
        kwargs = cb.message.answer_audio.await_args.kwargs
        self.assertEqual((kwargs['title'], kwargs['performer']), ('Ночной <эфир>', 'Группа & Ко'))
        remove.assert_called_once_with('/tmp/track.webm')
        cb.message.delete.assert_awaited_once()
        self.assertEqual(music.music_pick_sessions, {})

    async def test_missing_file_is_reported_and_session_closed(self):
        self.session()
        cb = self.callback()
        with patch.object(music, 'download_audio_raw_sync', return_value=None):
            await music.music_callback(cb)
        self.assertIn('Не удалось скачать', cb.message.edit_text.await_args.args[0])
        self.assertEqual(music.music_pick_sessions, {})

    async def test_telegram_rejection_is_reported_and_file_cleaned(self):
        self.session()
        cb = self.callback()
        cb.message.answer_audio = AsyncMock(side_effect=RuntimeError('too big'))
        with patch.object(music, 'download_audio_raw_sync', return_value='/tmp/track.webm'), \
             patch.object(music.os.path, 'exists', return_value=True), \
             patch.object(music.os, 'remove') as remove:
            await music.music_callback(cb)
        self.assertIn('не принял', cb.message.edit_text.await_args.args[0])
        remove.assert_called_once_with('/tmp/track.webm')

    async def test_command_without_arguments_asks_for_a_name(self):
        msg = message('/song')
        with patch.object(music, 'handle_music_request', AsyncMock()) as handler:
            await music.cmd_song(msg, type('Cmd', (), {'args': None})())
        self.assertEqual(handler.await_args.args[1], '')


if __name__ == '__main__':
    unittest.main()
