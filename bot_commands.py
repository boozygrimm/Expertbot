from aiogram.types import BotCommand


USER_PRIVATE_COMMAND_SPECS = [
    ("help", "Справка по боту"),
    ("support", "Связаться с владельцем"),
    ("base", "🏕 Главный Штаб"),
]

ADMIN_PRIVATE_COMMAND_SPECS = [
    ("help", "Справка"),
    ("img", "Генерация ИИ"),
    ("cron", "⏱ Настройка отчетов"),
    ("chats", "🔐 Управление чатами"),
    ("support", "Связаться с владельцем"),
    ("base", "🏕 Главный Штаб"),
    ("regen_quests", "Перезапуск квестов"),
    ("lore_edit", "Редактор Наследия"),
    ("lore_help", "Справка по лору"),
]

GROUP_COMMAND_SPECS = [
    ("help", "Справка"),
    ("img", "Нарисовать картинку (2 беспл/день, далее 250 очков)"),
    ("gossip", "Сплетни чата"),
    ("vis", "Виселица"),
    ("pole", "Поле чудес"),
    ("topgame", "Топ игроков"),
    ("stop", "Остановить игру"),
    ("voice", "Настройка голосовых"),
    ("panel", "Панель модулей чата"),
]


def build_bot_commands(specs: list[tuple[str, str]]) -> list[BotCommand]:
    return [BotCommand(command=command, description=description) for command, description in specs]
