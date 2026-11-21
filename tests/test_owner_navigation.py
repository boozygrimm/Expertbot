"""Every private-chat screen must be escapable.

The owner's menus are button-only, so a keyboard without «Назад»/«Закрыть»
strands the user in a dead end. These tests pin the shared navigation helper
and check each top-level screen for a way out.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from aiogram import types
from mongomock_motor import AsyncMongoMockClient

from handlers import owner_control_panel as panel, news_owner_panel
from handlers.owner_nav import CLOSE_CALLBACK, add_nav, ensure_nav, section_title


def callbacks(markup):
    return {button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data}


def labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def markup_of(rows):
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows])


class NavigationHelperTests(unittest.TestCase):
    def test_add_nav_puts_close_last_and_refresh_above_it(self):
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text='Действие', callback_data='x:1')
        add_nav(builder, back='x:back', refresh='x:refresh')
        markup = builder.as_markup()
        self.assertEqual([len(row) for row in markup.inline_keyboard], [1, 1, 2])
        self.assertEqual(callbacks(markup), {'x:1', 'x:refresh', 'x:back', CLOSE_CALLBACK})
        self.assertEqual(markup.inline_keyboard[-1][1].text, '❌ Закрыть')

    def test_ensure_nav_only_fills_gaps_and_is_idempotent(self):
        # A screen that drew its own back/close keeps its own wording.
        own = markup_of([[('Действие', 'x:1')], [('‹ Своё', 'x:own'), ('Закрыть', CLOSE_CALLBACK)]])
        self.assertIs(ensure_nav(own, back='x:back'), own)
        # A bare keyboard gets both controls exactly once.
        bare = markup_of([[('Действие', 'x:1')]])
        filled = ensure_nav(bare, back='x:back', refresh='x:refresh')
        self.assertEqual(callbacks(filled), {'x:1', 'x:refresh', 'x:back', CLOSE_CALLBACK})
        self.assertEqual(callbacks(ensure_nav(filled, back='x:back', refresh='x:refresh')),
                         {'x:1', 'x:refresh', 'x:back', CLOSE_CALLBACK})
        # Non-keyboard markup is passed through untouched (e.g. url-only rows).
        self.assertIsNone(ensure_nav(None, back='x:back'))

    def test_section_title_shows_the_path(self):
        self.assertIn('Пульт › Новости', section_title('📰', 'Новости', 'Пульт'))
        self.assertIn('Пульт › Новости › Сценарии',
                      section_title('📝', 'Сценарии', 'Пульт', 'Новости'))


class PrivateScreensTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        fake = SimpleNamespace(db=self.db, settings=self.db.settings)
        for module in (panel, news_owner_panel):
            p = patch.object(module, 'db', fake)
            p.start()
            self.addCleanup(p.stop)
        self.message = SimpleNamespace(chat=SimpleNamespace(type='private', id=panel.ADMIN_ID), message_id=77,
                                       answer=AsyncMock(), edit_text=AsyncMock(), delete=AsyncMock())
        self.callback = SimpleNamespace(from_user=SimpleNamespace(id=panel.ADMIN_ID), message=self.message,
                                        data='', answer=AsyncMock())
        self.state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={}))

    async def test_every_panel_screen_has_back_close_and_refresh(self):
        screens = ['home', 'group_menu', 'alchemy', 'alchemy_ref', 'alchemy_admin', 'media', 'help']
        for screen in screens:
            self.callback.data = 'owner_panel:' + screen
            await panel.control_action(self.callback, self.state)
            markup = self.message.edit_text.await_args.kwargs['reply_markup']
            with self.subTest(screen=screen):
                self.assertIn(CLOSE_CALLBACK, callbacks(markup))
                self.assertIn('owner_panel:' + screen, callbacks(markup), 'нет «Обновить»')
                back = callbacks(markup) & {'owner_panel:home', 'owner_panel:alchemy', 'owner_panel:media'}
                self.assertTrue(back, 'нет «Назад»')

    async def test_media_subscreens_and_status_return_to_their_own_section(self):
        for route, back in (('images', 'owner_panel:media'), ('search', 'owner_panel:media'),
                            ('status', 'owner_panel:home')):
            self.callback.data = 'owner_panel:' + route
            await panel.control_action(self.callback, self.state)
            markup = self.message.edit_text.await_args.kwargs['reply_markup']
            with self.subTest(route=route):
                self.assertIn(back, callbacks(markup))
                self.assertIn(CLOSE_CALLBACK, callbacks(markup))
                self.assertIn('owner_panel:' + route, callbacks(markup))

    async def test_screens_name_their_position_in_the_path(self):
        self.callback.data = 'owner_panel:alchemy_ref'
        await panel.control_action(self.callback, self.state)
        self.assertIn('Пульт › Алхимия › Рецепты и справка', self.message.edit_text.await_args.args[0])

    async def test_home_reports_counts_and_points_at_every_section(self):
        await self.db.news_owner_drafts.insert_one({'owner_id': panel.ADMIN_ID, 'stage': 'draft_ready'})
        with patch.object(panel.owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
            text, markup = await panel.home_content()
        self.assertIn('готово к проверке: 1', text)
        self.assertTrue({'owner_panel:news', 'gnews:home', 'news_panel:scripts', 'news_panel:editions',
                         'ovideo:open', 'owner_panel:status', 'owner_panel:alchemy', 'owner_panel:group_menu',
                         'owner_panel:media', 'owner_panel:help'} <= callbacks(markup))
        self.assertIn('❌ Закрыть', labels(markup))
        self.assertIn('🔄 Обновить', labels(markup))

    async def test_news_screens_keep_a_way_back_to_the_panel(self):
        for section in ('collection', 'scripts', 'editions', 'settings'):
            text, markup = await news_owner_panel.section_content(section)
            with self.subTest(section=section):
                self.assertIn('news_panel:home', callbacks(markup))
                self.assertIn(CLOSE_CALLBACK, callbacks(markup))
                self.assertIn('news_panel:' + section, callbacks(markup))
                self.assertIn('Пульт › Новости', text)


if __name__ == '__main__':
    unittest.main()
