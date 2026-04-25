# Technical Overview

## Назначение

`ExpertBot` это Telegram-бот с несколькими подсистемами:

- conversational assistant `Экс`
- LLM-backed utility features
- chat logging and reporting
- mini-games
- alchemy meta-game
- admin/chat authorization tooling

## Runtime Architecture

### Entry Point

Основной процесс запускается через [main.py](/C:/bugbi/main.py).

На старте приложение:

1. настраивает логирование
2. вызывает `db.setup()`
3. инициализирует lore и personas
4. регистрирует команды Telegram
5. собирает `Dispatcher`
6. подключает middleware
7. подключает routers в фиксированном порядке
8. запускает `APScheduler`
9. поднимает `aiohttp` healthcheck
10. стартует `dp.start_polling(bot)`

### HTTP Layer

Есть минимальный healthcheck:

- `GET /` -> `200 OK`

Назначение — liveness probe и совместимость с платформами вроде Hugging Face Spaces.

### Telegram Layer

Bot singleton создаётся в [bot_instance.py](/C:/bugbi/bot_instance.py) из `TELEGRAM_TOKEN`.

Используемый фреймворк:

- `aiogram`

### Middleware Chain

Порядок message middleware:

1. `AuthMiddleware`
2. `ChatLoggerMiddleware`
3. `FeatureAccessMiddleware`

Для callback queries подключён `FeatureAccessMiddleware`.

Это означает:

- сначала проверяется право чата на использование бота
- затем логируются сообщения
- затем проверяется доступность фичи в панели модулей

## Module Map

### Core Files

- [main.py](/C:/bugbi/main.py) — bootstrap, routers, scheduler
- [config.py](/C:/bugbi/config.py) — env config, MSK timezone helper
- [database.py](/C:/bugbi/database.py) — Mongo client и коллекции
- [ai_logic.py](/C:/bugbi/ai_logic.py) — LLM, vision, transcription helpers, fallback chains
- [personas.py](/C:/bugbi/personas.py) — seed/update personas

### Main Functional Routers

- `handlers/ex_logic.py` — главный conversational router
- `handlers/web_search.py` — поиск через DDGS + summarization
- `handlers/music.py` — поиск и выдача музыки
- `handlers/image_gen.py` — генерация изображений
- `handlers/voice_rec.py` — расшифровка голосовых и voice-to-Ex handoff
- `handlers/reports.py` — сводки и персонажи-эксперты
- `handlers/pole_chudes.py`, `handlers/viselitsa.py`, `handlers/anachronism.py` — игры
- `handlers/admin_panel.py` — панель модулей чата
- `handlers/admin_cron.py` — ежедневные отчёты
- `handlers/auth.py` — авторизация групп
- `handlers/logger.py` — запись истории чата
- `handlers/alc_*` — алхимическая подсистема

## Critical Router Ordering

Порядок `dp.include_router(...)` в [main.py](/C:/bugbi/main.py) функционально важен.

Особенно важно:

- `alc_base.router` подключён раньше, чтобы перехватывать deep link / стартовые команды алхимии
- `ex_logic.router` подключён последним как общий fallback-обработчик

Любая перестановка routers должна считаться потенциально опасным изменением поведения.

## Data Model

### MongoDB Connection

Подключение и коллекции объявлены в [database.py](/C:/bugbi/database.py).

Текущая база:

- `beleyakov_db`

### Collections

- `messages`
  Назначение: история сообщений чатов для аналитики, gossip, ответов по контексту.
  Особенность: TTL index на `createdAt`, срок жизни 7 дней.

- `personas`
  Назначение: профили personalities ассистента.
  Индекс: `p_id` unique.

- `settings`
  Назначение: feature flags, daily report, voice settings и прочая конфигурация чатов.
  Индекс: `(setting, chat_id)` unique.

- `auth_chats`
  Назначение: доступ групп к боту.
  Индекс: `chat_id` unique.

- `game_data`
  Назначение: игровые лимиты и технические данные игр.

- `economy`
  Назначение: очки и операции экономики.

- `pole_stats`
  Назначение: статистика по игре "Поле чудес".

- `pole_history`
  Назначение: история слов для игровых ограничений и антидубликатов.

- `profiles`
  Назначение: профили social RP.

- `alchemy_players`
  Назначение: глобальные профили игроков алхимии.
  Индекс: `user_id` unique.

- `alchemy_lore`
  Назначение: lore-объекты алхимии.
  Индекс: `id` unique.

## External Services

### LLM Providers

В проекте используется несколько провайдеров с fallback-логикой:

- Gemini
- Groq
- OpenRouter

Основные env:

- `GEMINI_KEYS`
- `GEMINI_GAME_KEY`
- `GROQ_KEYS`
- `OPENROUTER_KEYS`

В коде есть fallback на старые имена:

- `GEMINI_API_KEYS`
- `GROQ_API_KEY`

### Search

- `ddgs` / DuckDuckGo text search

### Media / Audio / Music

- `yt-dlp`
- `ffmpeg`
- внешние источники для музыкального поиска

### Image Generation

- Pollinations image endpoint

## Scheduler Jobs

Планировщик создаётся в [main.py](/C:/bugbi/main.py).

Текущие фоновые задачи:

- `check_cron_time` — раз в минуту
- `check_alchemy_stamina` — каждые 10 минут
- `collect_production_loot` — каждый час
- `process_heritage_income` — ежедневно `03:05` МСК
- `process_barracks_upkeep` — ежедневно `03:10` МСК
- `process_expeditions_tick` — каждую минуту
- `notify_arrived_expeditions` — каждые 2 минуты
- `process_random_fort_events` — каждые 2 часа в `:15`

## State Management

### In-Memory State

Часть игровых данных хранится в оперативной памяти процесса:

- `active_games` в `pole_chudes.py`
- `active_hangman_games` в `viselitsa.py`
- временные сессии выбора музыки

Следствие:

- рестарт процесса завершает активные игровые сессии
- это нужно учитывать при деплое и рестартах

### Persistent State

Долгоживущее состояние хранится в MongoDB:

- история сообщений
- feature flags
- экономика
- прогресс алхимии
- расписания
- авторизация чатов

## Assistant Flow

Основной conversational flow реализован в `handlers/ex_logic.py`.

Высокоуровневый сценарий:

1. определить, является ли сообщение триггером для Экса
2. классифицировать намерение
3. при необходимости делегировать в music/search/image/game/report handlers
4. иначе собрать контекст и сгенерировать ответ модели

## Voice Flow

`handlers/voice_rec.py`:

1. проверяет включён ли `auto_voice` для чата
2. скачивает voice/video_note
3. отправляет на распознавание
4. публикует расшифровку
5. если распознанный текст начинается с обращения к Эксу, прокидывает его в `handle_ex_request`

## Feature Flags

Панель модулей реализована в `handlers/admin_panel.py`.

Поддерживаемые фичи:

- assistant
- images
- music
- voice
- reports
- games
- social
- social_18
- alchemy
- daily_reports

Флаги хранятся в `settings`.

## Deployment Notes

### Local

Запуск:

```powershell
python main.py
```

### Docker

Сборка и запуск описаны в [README.md](/C:/bugbi/README.md).

### Hosted Environments

В коде учитываются переменные:

- `PORT`
- `SPACE_ID`

Это указывает на использование cloud/container hosting, включая Hugging Face Spaces.

## Known Technical Constraints

### Current Strengths

- рабочее разделение по handlers
- MongoDB уже используется как основное persistent storage
- есть middleware слой и feature gating
- есть healthcheck и scheduler

### Current Risks

- `ai_logic.py` перегружен обязанностями
- архитектура частично опирается на порядок routers
- проект почти без автотестов
- есть глобальные singleton-style зависимости (`bot`, `db`)
- часть поведения держится на широких `try/except`

## Change Safety Rules

Если нужно править проект без высокого риска регрессии:

1. не менять порядок routers без причины
2. не трогать fallback-цепочки моделей одним большим рефакторингом
3. не менять сразу и handlers, и persistence, и prompts в одном коммите
4. правки делать малыми патчами с синтаксической проверкой
5. отдельно тестировать игры, голос, музыку и daily reports

## Recommended Next Documentation

Если продолжать наведение порядка, следующими документами стоит добавить:

- `docs/ALCHEMY.md`
- `docs/DEPLOYMENT.md`
- `docs/MONGO_SCHEMA.md`
- `docs/RUNBOOK.md`

