import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from handlers import reports


class PersonaSelectionTests(unittest.IsolatedAsyncioTestCase):
    def make_db(self, personas):
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=personas))
        return SimpleNamespace(personas=SimpleNamespace(find=MagicMock(return_value=cursor)))

    async def test_single_persona_starts_report_without_keyboard(self):
        persona = {'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖'}
        db = self.make_db([persona])
        message = SimpleNamespace(answer=AsyncMock())
        with patch.object(reports, 'db', db), \
             patch.object(reports, 'run_expert_report', new_callable=AsyncMock) as run:
            await reports.send_persona_selection(message, 42)
        run.assert_awaited_once_with(message, persona, 42)
        message.answer.assert_not_awaited()

    async def test_multiple_personas_still_get_selection(self):
        personas = [{'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖'},
                    {'p_id': 'other', 'name': 'Другой', 'emoji': '🧠'}]
        db = self.make_db(personas)
        message = SimpleNamespace(answer=AsyncMock())
        with patch.object(reports, 'db', db), \
             patch.object(reports, 'run_expert_report', new_callable=AsyncMock) as run:
            await reports.send_persona_selection(message, 150)
        run.assert_not_awaited()
        message.answer.assert_awaited_once()
        markup = message.answer.await_args.kwargs['reply_markup']
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertEqual([b.callback_data for b in buttons], ['rep:bender:150', 'rep:other:150'])

    async def test_no_personas_reports_error(self):
        db = self.make_db([])
        message = SimpleNamespace(answer=AsyncMock())
        with patch.object(reports, 'db', db), \
             patch.object(reports, 'run_expert_report', new_callable=AsyncMock) as run:
            await reports.send_persona_selection(message, 50)
        run.assert_not_awaited()
        self.assertIn('нет ни одного эксперта', message.answer.await_args.args[0])

    async def test_gossip_command_passes_clamped_count(self):
        message = SimpleNamespace(text='/gossip 9000')
        with patch.object(reports, 'send_persona_selection', new_callable=AsyncMock) as select:
            await reports.gossip_cmd(message)
        select.assert_awaited_once_with(message, 3000)

    async def test_single_persona_runs_existing_report(self):
        persona = {'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖', 'loading': 'Думаю'}
        message = SimpleNamespace(chat=SimpleNamespace(id=-1001), answer=AsyncMock())
        status = SimpleNamespace(delete=AsyncMock(), edit_text=AsyncMock())
        sent = SimpleNamespace(message_id=12)
        message.answer.side_effect = [status, sent]
        history_cursor = MagicMock()
        history_cursor.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[
            {'user_name': 'Тестер', 'text': 'Привет'}
        ])
        db = self.make_db([persona])
        db.messages = SimpleNamespace(find=MagicMock(return_value=history_cursor), insert_one=AsyncMock())
        with patch.object(reports, 'db', db), \
             patch.object(reports, 'get_expert_report', new=AsyncMock(return_value='Всем привет')) as generate, \
             patch.object(reports.asyncio, 'sleep', new=AsyncMock()):
            await reports.send_persona_selection(message, 20)
        generate.assert_awaited_once_with('Тестер: Привет', 'bender')
        status.delete.assert_awaited_once()
        self.assertIn('Всем привет', message.answer.await_args_list[1].args[0])
        db.messages.insert_one.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
