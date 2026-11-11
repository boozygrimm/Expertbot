# ExpertBot

Telegram-бот на `aiogram` с несколькими режимами работы:

- чат-ассистент `Экс`
- генерация изображений
- поиск и сводки по чату
- музыка и расшифровка голосовых
- мини-игры
- крупный игровой модуль алхимии

Проект работает как long polling бот и одновременно поднимает HTTP healthcheck через `aiohttp`.

## Текущее состояние

Проект уже переведен на более аккуратную стартовую конфигурацию:

- `main.py` теперь в основном orchestrates startup
- `app_setup.py` собирает `Dispatcher`, scheduler и HTTP healthcheck
- `bot_commands.py` хранит Telegram-команды по scope и их порядок
- для чистой игровой и startup-логики добавлен лёгкий unit-test контур на `unittest`

Сейчас в проекте есть быстрые локальные тесты без реальной БД и без сетевых вызовов.

## Быстрый старт

### 1. Требования

- Python `3.12+`
- MongoDB
- `ffmpeg`
- зависимости из `requirements.txt`

Для музыкального модуля в Docker уже ставятся `nodejs` и `ffmpeg`.

Морфологию для Social RP (склонение ников и глаголов по полу) обеспечивает
`pymorphy3` — поддерживаемый форк `pymorphy2` с тем же API; словари
`pymorphy3-dicts-ru` ставятся вместе с ним. Менять на `pymorphy2` не нужно:
версия 0.9.1 не работает на Python 3.11+ (внутри вызывается удалённый
`inspect.getargspec`), из-за чего модуль `handlers/social_rp.py` не импортируется
и бот не стартует. Если морфология недоступна, Social RP автоматически
переключается на упрощённое склонение — это не ошибка, а запланированный fallback.

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

### Модели AI-провайдеров

Все идентификаторы моделей вынесены в [models.py](models.py) — это единая точка,
которую нужно править при очередной ротации моделей у провайдеров.

| Назначение | Переменная | Значение по умолчанию |
| --- | --- | --- |
| Быстрые запросы Groq (игры, нормализация, промпты для картинок) | `GROQ_FAST_MODELS` | `openai/gpt-oss-20b` |
| Основные запросы Groq (диалоги, отчёты, экспедиции) | `GROQ_STRONG_MODELS` | `openai/gpt-oss-120b`, `qwen/qwen3.8-27b` |
| Служебный запрос для генерации картинок | `GROQ_IMAGE_PROMPT_MODEL` | `openai/gpt-oss-20b` |
| Расшифровка голосовых | `GROQ_TRANSCRIBE_MODELS` | `whisper-large-v3`, `whisper-large-v3-turbo` |
| Gemini (основной провайдер) | `GEMINI_CHAT_MODEL` | `gemini-3-flash-preview` |
| OpenRouter: текст | `OPENROUTER_TEXT_MODEL` | `google/gemma-3-27b-it:free` |
| OpenRouter: зрение | `OPENROUTER_VISION_MODEL` | `qwen/qwen3-vl-30b-thinking:free` |
| OpenRouter: сводки чата | `OPENROUTER_SUMMARY_MODEL` | `qwen/qwen-2.5-72b-instruct:free` |

Списки моделей задаются через запятую: порядок элементов = порядок фолбэка.
Если модель отвечает `400/404` (снята с публикации или недоступна на тарифе)
или ключ отвечает `401/403`, запрос автоматически уходит на следующую модель,
а не падает.

Особенности текущей линейки Groq:

- `openai/gpt-oss-120b` и `openai/gpt-oss-20b` — production-модели, стабильная замена
  снятым Llama;
- `qwen/qwen3.8-27b` — **preview**-модель: Groq может снять её с коротким уведомлением,
  поэтому она стоит в цепочке второй, после production-модели, и используется только
  как запасной вариант;
- базовые лимиты Groq для этих моделей — 30 RPM / 1 000 RPD / 8K TPM / 200K TPD
  (в доках таблица описывает базовые лимиты Developer-плана, точные значения своей
  организации видны в `console.groq.com/settings/limits`);
- reasoning-токены у `gpt-oss` тарифицируются как output и входят в TPM, поэтому
  в быстром контуре используется `reasoning_effort=low`, а не `medium`.

#### Миграция с устаревших моделей Groq (ноябрь 2026)

Groq вывел из free/developer-тарифов прежний парк моделей, запросы к этим ID
возвращают ошибку ([депрекации Groq](https://console.groq.com/docs/deprecations)):

| Было в коде | Отключено | Рекомендация Groq | В проекте заменено на |
| --- | --- | --- | --- |
| `llama-3.1-8b-instant` | 16.08.2026 | `openai/gpt-oss-20b` | `openai/gpt-oss-20b` (быстрый контур) |
| `llama-3.3-70b-versatile` | 16.08.2026 | `openai/gpt-oss-120b` | `openai/gpt-oss-120b` (основной контур) |
| `qwen/qwen3-32b` | 17.07.2026 | `openai/gpt-oss-120b` | `qwen/qwen3.8-27b` (запасной в основном контуре) |
| `meta-llama/llama-4-scout-17b-16e-instruct` | 17.07.2026 | `openai/gpt-oss-120b` | `openai/gpt-oss-20b` (модель сидела в быстром контуре, там нужна скорость) |

Попутно учтены особенности новых моделей:

- вместо deprecated `max_tokens` отправляется `max_completion_tokens`;
- для `gpt-oss` передаётся `temperature=1` — другие значения модель отвергает;
- reasoning-модели (`gpt-oss`, `qwen3.x`) запрашиваются с `reasoning_format=hidden`
  и `reasoning_effort` (`low`/`medium` у gpt-oss, `none`/`low` у qwen), чтобы
  цепочка рассуждений не съедала лимит токенов и не обрезала ответ.

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
main.py               Точка входа и orchestration запуска
app_setup.py          Сборка dispatcher, scheduler и healthcheck-сервера
bot_commands.py       Команды Telegram по scope и их порядок
config.py             Чтение env и время по МСК
models.py             Реестр идентификаторов моделей всех AI-провайдеров
database.py           MongoDB client и коллекции
bot_instance.py       Singleton Bot
ai_logic.py           LLM/vision/audio utility layer и fallback-логика
personas.py           Базовые личности для Экса
handlers/             Telegram handlers и игровые модули
tests/                Быстрые unit-тесты на чистую логику
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

Подробности по архитектуре и данным: [PROJECT_ANALYSIS.md](PROJECT_ANALYSIS.md)

## Фоновые задачи

Через `APScheduler` выполняются:

- проверка времени ежедневных отчётов раз в минуту
- регенерация/тик алхимии
- доходы и upkeep
- тики экспедиций и рейдов

Точный список и расписание описаны в техдоке.

## Тесты

Проект использует лёгкий тестовый контур на встроенном `unittest`.

Запуск:

```powershell
python -m unittest discover -s tests -v
```

Что сейчас уже покрыто:

- чистая логика алхимии и базовых расчётов
- бойцы, апгрейды и миграция старых данных
- статусы штаба, стройка, утренние отчёты
- расчёты экспедиций, рейдов и квестов
- startup-слой: команды, scheduler, healthcheck

Тесты специально держатся быстрыми:

- без записи файлов
- без реального MongoDB
- без сетевых вызовов
- без тяжёлых интеграционных прогонов

## Важные ограничения проекта

- порядок подключения роутеров в `main.py` важен
- порядок `include_router(...)` сохранён через `app_setup.py` и менять его нужно осознанно
- часть состояния игр хранится в памяти процесса и сбрасывается при рестарте
- `ai_logic.py` является крупным инфраструктурным модулем и содержит fallback-цепочки нескольких провайдеров
- автотесты уже есть, но покрытие всё ещё точечное, поэтому любые изменения лучше делать малыми патчами

## Telegram-команды

Команды для Telegram-меню задаются в [bot_commands.py](bot_commands.py).

- `USER_PRIVATE_COMMAND_SPECS` — обычные ЛС
- `ADMIN_PRIVATE_COMMAND_SPECS` — owner/admin ЛС scope
- `GROUP_COMMAND_SPECS` — группы

Порядок команд в меню определяется порядком элементов в этих списках.

Важно: для `setMyCommands` Telegram принимает только команды в формате `a-z`, `0-9`, `_`. Кириллические slash-команды можно поддерживать как текстовые aliases в хендлерах, но не как menu-команды.

## Документация

- [Полный анализ проекта](PROJECT_ANALYSIS.md)
