# ExpertBot

Telegram-бот на `aiogram` с несколькими режимами работы:

- чат-ассистент `Экс`
- генерация изображений
- поиск и сводки по чату
- музыка и расшифровка голосовых
- мини-игры
- крупный игровой модуль алхимии

Проект работает как long polling бот и одновременно поднимает HTTP healthcheck через `aiohttp`.

## Быстрый старт

### 1. Требования

- Python `3.12+`
- MongoDB
- `ffmpeg`
- зависимости из `requirements.txt`

Для музыкального модуля в Docker уже ставятся `nodejs` и `ffmpeg`.

### 2. Переменные окружения

Скопируйте `.env.example` в `.env` и заполните значения:

```env
TELEGRAM_TOKEN=
MONGO_URI=
ADMIN_ID=
GEMINI_KEYS=
GROQ_KEYS=
GEMINI_GAME_KEY=
OPENROUTER_KEYS=
PORT=7860
```

Поддерживаются также альтернативные имена:

- `GEMINI_API_KEYS` вместо `GEMINI_KEYS`
- `GROQ_API_KEY` вместо `GROQ_KEYS`

### 3. Локальный запуск

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

После старта бот:

- подключается к MongoDB
- поднимает индексы
- инициализирует личности
- регистрирует Telegram-команды
- запускает фоновые задачи
- открывает healthcheck на `GET /`

### 4. Docker

```powershell
docker build -t expertbot .
docker run --env-file .env -p 7860:7860 expertbot
```

## Структура проекта

```text
main.py               Точка входа, роутеры, middleware, scheduler, healthcheck
config.py             Чтение env и время по МСК
database.py           MongoDB client и коллекции
bot_instance.py       Singleton Bot
ai_logic.py           LLM/vision/audio utility layer и fallback-логика
personas.py           Базовые личности для Экса
handlers/             Telegram handlers и игровые модули
docs/                 Техническая документация
```

Ключевые группы модулей в `handlers/`:

- `ex_logic.py` — основной интеллект и маршрутизация запросов к Эксу
- `admin_panel.py`, `admin_cron.py`, `auth.py` — админка, доступ в чаты, расписания
- `reports.py`, `web_search.py`, `image_gen.py`, `music.py`, `voice_rec.py` — сервисные пользовательские функции
- `pole_chudes.py`, `viselitsa.py`, `anachronism.py`, `game_stats.py` — мини-игры
- `alc_*` — алхимический игровой контур

## База данных

Основные коллекции:

- `messages` — история сообщений чатов, TTL 7 дней
- `settings` — настройки чатов и модулей
- `personas` — личности ассистента
- `auth_chats` — авторизация групп
- `game_data`, `economy`, `pole_history`, `pole_stats`
- `profiles` — Social RP
- `alchemy_players`, `alchemy_lore` — алхимия

Подробности по архитектуре и данным: [docs/TECHNICAL_OVERVIEW.md](/C:/bugbi/docs/TECHNICAL_OVERVIEW.md)

## Фоновые задачи

Через `APScheduler` выполняются:

- проверка времени ежедневных отчётов раз в минуту
- регенерация/тик алхимии
- доходы и upkeep
- тики экспедиций и рейдов

Точный список и расписание описаны в техдоке.

## Важные ограничения проекта

- порядок подключения роутеров в `main.py` важен
- часть состояния игр хранится в памяти процесса и сбрасывается при рестарте
- `ai_logic.py` является крупным инфраструктурным модулем и содержит fallback-цепочки нескольких провайдеров
- проект пока почти без автотестов, поэтому любые изменения лучше делать малыми патчами

## Документация

- [Технический обзор](docs/TECHNICAL_OVERVIEW.md)
- [Алхимия](docs/ALCHEMY.md)
- [Runbook](docs/RUNBOOK.md)
