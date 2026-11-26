"""Keep the command reference and public help aligned with the registered code."""
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from bot_commands import ADMIN_PRIVATE_COMMAND_SPECS, GROUP_COMMAND_SPECS, USER_PRIVATE_COMMAND_SPECS
from bender_triggers import parse_video_request
from handlers import admin_panel, ex_logic
from video_prototype import pipeline

ROOT = Path(__file__).resolve().parents[1]


def registered_command_literals():
    """Collect literal aiogram Command filters, including the top-level help."""
    files = [ROOT / 'app_setup.py', *(ROOT / 'handlers').glob('*.py')]
    result = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'Command'):
                continue
            values = list(node.args)
            for keyword in node.keywords:
                if keyword.arg == 'commands' and isinstance(keyword.value, (ast.List, ast.Tuple)):
                    values.extend(keyword.value.elts)
            result.update(value.value for value in values
                          if isinstance(value, ast.Constant) and isinstance(value.value, str))
    return result


class DocumentationTests(unittest.TestCase):
    def test_every_registered_command_is_covered_and_menu_is_registered(self):
        reference = (ROOT / 'COMMANDS.md').read_text(encoding='utf-8')
        commands = registered_command_literals()
        for command in commands | {'vest', 'сплетни', 'вещай', 'выбор'}:
            with self.subTest(command=command):
                self.assertRegex(reference, r'/' + re.escape(command) + r'(?![\w])')
        menu = USER_PRIVATE_COMMAND_SPECS + ADMIN_PRIVATE_COMMAND_SPECS + GROUP_COMMAND_SPECS
        for command, description in menu:
            with self.subTest(menu=command):
                self.assertIn(command, commands)
                self.assertTrue(1 <= len(description) <= 256)
                self.assertRegex(reference, r'/' + re.escape(command) + r'(?![\w])')
        for specs in (USER_PRIVATE_COMMAND_SPECS, ADMIN_PRIVATE_COMMAND_SPECS, GROUP_COMMAND_SPECS):
            self.assertEqual(len(specs), len({command for command, _ in specs}))

    def test_command_filters_are_attached_to_real_dispatcher(self):
        # Separate process: configure_dispatcher attaches the singleton routers.
        script = '''
import json
from aiogram.filters import Command
from app_setup import configure_dispatcher
root = configure_dispatcher()
names = set()
for router in [root, *root.sub_routers]:
    for handler in router.message.handlers:
        for item in handler.filters or []:
            if isinstance(item.callback, Command):
                names.update(c for c in item.callback.commands if isinstance(c, str))
print(json.dumps(sorted(names), ensure_ascii=False))
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, text=True,
                                capture_output=True, timeout=30, check=True)
        attached = set(json.loads(result.stdout))
        self.assertTrue(registered_command_literals() <= attached)

    def test_local_documentation_links_exist(self):
        for filename in ('README.md', 'COMMANDS.md', 'PROJECT_ANALYSIS.md',
                         'VIDEO_NOTES_PLAN.md', 'tests/README.md', 'video_prototype/README.md'):
            path = ROOT / filename
            for target in re.findall(r'\]\(([^ )]+)', path.read_text(encoding='utf-8')):
                if target.startswith(('https:', 'http:', 'mailto:', '#')):
                    continue
                with self.subTest(document=filename, target=target):
                    self.assertTrue((path.parent / target.split('#')[0]).exists())

    def test_documented_manual_pipeline_examples_parse_without_side_effects(self):
        args = pipeline.parse_args(['--audio', '/data/xvibe-test/proba-emotion.wav', '--no-send'])
        self.assertTrue(args.no_send)
        args = pipeline.parse_args(['--text', 'test', '--voice-only', '--no-send'])
        self.assertTrue(args.voice_only and args.no_send)
        args = pipeline.parse_args(['--text', 'test', '--channel-id', '-1001234567890',
                                    '--chat-id', '-1002345678901'])
        self.assertEqual(args.channel_id, -1001234567890)

    def test_documented_video_triggers(self):
        self.assertEqual(parse_video_request('/bender вопрос'), 'вопрос')
        self.assertEqual(parse_video_request('Экс, кружок вопрос'), 'вопрос')
        self.assertIsNone(parse_video_request('Экс, кружочки'))


class HelpTests(unittest.IsolatedAsyncioTestCase):
    async def test_literal_command_filters_accept_documented_plain_commands(self):
        from aiogram.filters import Command
        from aiogram.types import Message
        for command in sorted(registered_command_literals() - {'экс помощь'}):
            with self.subTest(command=command):
                msg = Message.model_validate({'message_id': 1, 'date': 1760000000,
                    'chat': {'id': -1001, 'type': 'supergroup'},
                    'from': {'id': 7, 'is_bot': False, 'first_name': 'Тестер'},
                    'text': '/' + command})
                self.assertTrue(await Command(command)(msg, bot=None))
        # An argument is not part of aiogram's command name.
        self.assertEqual(Command.extract_command('/экс помощь').command, 'экс')

    async def test_help_and_panel_details_fit_telegram_limit(self):
        flags = admin_panel.get_default_feature_flags()
        with patch.object(admin_panel, 'get_chat_feature_flags', new=AsyncMock(return_value=flags)), \
             patch.object(admin_panel, 'get_daily_report_time', new=AsyncMock(return_value='23:59')):
            text = await admin_panel.build_feature_details_text(-1001)
        for value in (ex_logic.HELP_TEXT, text):
            self.assertLessEqual(len(value.encode('utf-16-le')) // 2, 4096)
        self.assertIn('3 минуты', text)
        self.assertIn('/voice off', text)
        self.assertIn('/bender', text)
        self.assertIn('для кружка выбор остаётся', text)

    async def test_documented_deterministic_intents(self):
        cases = [('найди инфу про Пифагора', 'SEARCH'), ('погода Питер', 'WEATHER'),
                 ('найди песню Rammstein', 'MUSIC'), ('нарисуй кота', 'IMAGE')]
        for query, intent in cases:
            with self.subTest(query=query):
                actual, _ = await ex_logic.analyze_intent(query)
                self.assertEqual(actual, intent)
