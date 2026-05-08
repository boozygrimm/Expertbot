# ExpertBot — Полный анализ проекта

## Обзор

**ExpertBot** — это многофункциональный Telegram-бот с AI-ассистентом "Экс", играми и мета-игрой "Алхимия".

---

## Архитектура

### Технологический стек
- Python 3.14
- aiogram (Telegram Bot API)
- MongoDB (motor/asyncio)
- APScheduler (фоновые задачи)
- aiohttp (HTTP healthcheck)

### Основные компоненты

| Компонент | Файл | Назначение |
|-----------|------|------------|
| Entry point | `main.py` | Bootstrap, routers, scheduler |
| Config | `config.py` | Env vars, timezone (MSK) |
| Database | `database.py` | MongoDB client, collections |
| AI Logic | `ai_logic.py` | LLM, vision, transcription, fallback chains |
| Bot instance | `bot_instance.py` | Telegram bot singleton |

---

## Функциональные модули

### Handlers (28 файлов)

| Файл | Назначение |
|------|-----------|
| `ex_logic.py` | Главный conversational router (Экс) |
| `web_search.py` | Поиск через DuckDuckGo + summarization |
| `music.py` | Поиск и выдача музыки |
| `image_gen.py` | Генерация изображений |
| `voice_rec.py` | Расшифровка голосовых |
| `reports.py` | Сводки и персонажи-эксперты |
| `pole_chudes.py` | Игра "Поле чудес" |
| `viselitsa.py` | Игра "Виселица" |
| `anachronism.py` | Игра "Анахронизм" |
| `admin_panel.py` | Панель модулей чата |
| `admin_cron.py` | Ежедневные отчёты |
| `auth.py` | Авторизация групп |
| `logger.py` | Запись истории чата |
| `alc_core.py` | Базовые константы, шаблон игрока, инициализация, походы, кулдауны |
| `alc_base.py` | Главный штаб, сводка по базе, deep links |
| `alc_build.py` | Постройки, казармы, бойцы, развитие базы |
| `alc_lab.py` | Лаборатория и смешивание |
| `alc_crucible.py` | Тигель (массовый крафт) и Реторта (разложение) |
| `alc_production.py` | Производственные линии |
| `alc_expeditions.py` | Экспедиции, состав отряда, тики событий |
| `alc_raids.py` | Рейды по живым/неживым целям, форт, пленники |
| `alc_quests.py` | Доска заданий |
| `alc_achievements.py` | Достижения и награды |
| `alc_heritage.py` | История мира, фрагменты, стенды, постоянные бонусы |
| `alc_tasks.py` | Фоновые задачи алхимии |
| `alc_fighters.py` | Система бойцов |
| `alc_recipes.py` | Рецепты и элементы |
| `alc_intro.py` | Обучение/интро |
| `social_rp.py` | Социальное RP |

---

## База данных

### MongoDB (`beleyakov_db`)

| Коллекция | Назначение |
|-----------|------------|
| `messages` | История чатов (TTL 7 дней) |
| `personas` | Профили ассистента |
| `settings` | Feature flags, конфигурация чатов |
| `auth_chats` | Авторизованные группы |
| `game_data` | Игровые лимиты и технические данные |
| `economy` | Очки и операции экономики |
| `pole_stats` | Статистика по игре "Поле чудес" |
| `pole_history` | История слов для игровых ограничений |
| `profiles` | Профили social RP |
| `alchemy_players` | Глобальные профили игроков алхимии |
| `alchemy_lore` | Lore-объекты алхимии |

---

## Внешние сервисы

### LLM Providers (fallback chain)
- Gemini (основной)
- Groq
- OpenRouter

### Другие
- DuckDuckGo Search
- Pollinations (image generation)
- yt-dlp (music)
- ffmpeg (audio processing)

---

## Scheduler Jobs

| Задача | Частота | Назначение |
|--------|---------|------------|
| `check_cron_time` | Раз в минуту | Проверка времени для daily reports |
| `check_alchemy_stamina` | Каждые 10 минут | Уведомление о восстановлении выносливости |
| `collect_production_loot` | Каждый час | Сбор ресурсов с линий |
| `process_heritage_income` | Ежедневно 03:05 МСК | Ежедневные бонусы наследия |
| `process_barracks_upkeep` | Ежедневно 03:10 МСК | Списание энергии за бойцов |
| `process_expeditions_tick` | Каждую минуту | Тики экспедиций |
| `notify_arrived_expeditions` | Каждые 2 минуты | Уведомление о прибытии |
| `process_random_fort_events` | Каждые 2 часа | События форта |

---

## Подсистема Алхимия

### Обзор

Полноценная игровая подсистема с:
- Профилем игрока, инвентарём и статистикой
- Экономикой ресурсов
- Системой построек и развития базы
- Лабораторией и производством
- Системой бойцов, экспедиций и рейдов
- Lore/heritage системой
- Достижениями и daily upkeep

### Профиль игрока

Хранится в `db.alchemy_players`, создаётся через `get_or_create_player()`.

**Ключевые поля:**
- `inventory` — инвентарь предметов
- `discovered` — открытые рецепты
- `lore_fragments` — фрагменты лора
- `active_stands` — активные стенды наследия
- `stats` — статы (stamina, luck)
- `buildings` — уровни построек (storage, lab, altar, guild, barracks, fort, heritage_center)
- `fighters` — список бойцов
- `active_expeditions` — активные экспедиции
- `raid_squad` — рейд-отряд
- `fort_defense` — оборона форта
- `worlds` — созданные миры/биомы

### Система рецептов

**Базовые элементы:**
- w 💨 Воздух, f 🔥 Огонь, e 🪨 Земля, a 💧 Вода

**Рабочие духи:**
- spirit_f 🔥, spirit_w 💧, spirit_e 🪨, spirit_a 💨

**Боевые единицы (26 типов):**

Тир 1 (8 юнитов, upkeep=1, barracks_level=1):
- fire_swordsman 🔥 Огнемеч
- water_guard 💧 Водостраж
- earth_shielder 🪨 Щитоносец
- wind_scout 💨 Ветрослед
- ash_pikeman 🌋 Пеплокопь
- wave_spearman 🌊 Волникопь
- stone_axeman 🪓 Камнетёс
- storm_archer ⛈ Бурелук

Тир 2 (8 юнитов, upkeep=1, barracks_level=2):
- rider_f 🔥 Пламерейд
- rider_w 💧 Приливрейд
- rider_e 🪨 Скаларейд
- rider_a 💨 Вихререйд
- saboteur 💣 Сапёр
- medic 🩺 Медик
- engineer 🛠 Инженер
- sniper 🎯 Снайпер

Тир 3 (10 юнитов, upkeep=3, barracks_level=4-5):
- mech 🤖 Мех
- flame_tank 🔥 Огнетанк
- tesla_tank ⚡ Теслатанк
- hover 🚤 Ховер
- drone 🛸 Дрон
- artillery 🎯 Арта
- walker 🕷 Ходун
- juggernaut 🦾 Джаггер

### Система построек

| Здание | Уровни | Эффект |
|---------|--------|--------|
| Склад | 3 | 20→50→150→350 слотов |
| Лаборатория | 3 | Базовый крафт → Тигель → Реторта |
| Производство | 3 | 1→2→3 линии |
| Гильдия | 3 | -30мин/-1ч/-2ч к кд, +2/+5/+10 ресурсов |
| Казармы | 5 | Т1→Т2→Т3→Т4→Т5 юниты |
| Форт | 3 | Ров → Катапульта → Распылитель |
| Наследие | 5 | 1→2→3→4→5 стендов (x2 бонус на 5 ур.) |

### Система походов

**Кулдауны:**
- Базовый: 4 часа
- Guild 1: -30 мин, Guild 2: -1 ч, Guild 3+: -2 ч
- Минимум: 1 час

**Локации и дроп:**
- Река: w, sand, cane
- Горы: e, stone, metal
- Пещера: e, stone, energy, glass
- Вулкан: f, lava, brick

### Система бойцов

**Характеристики:**
- Бодрость: 3 (Т1) / 5 (Т2) / 7 (Т3)
- Восстановление: 1 бодрость за 6 часов
- Upkeep: 1 (Т1) / 2 (Т2) / 3 (Т3) энергии в день
- Риск смерти: 13% (Т1) / 9% (Т2) / 5% (Т3) при 0 бодрости

**Вместимость казарм:**
- Ур. 0: 0, Ур. 1: 8, Ур. 2: 14, Ур. 3: 20, Ур. 4: 28, Ур. 5: 36

### Система экспедиций

**Параметры:**
- Тик: 5 минут
- Базовая длительность: 1 час
- Минимальное время возврата: 5 минут
- Лимит команд: 1 (barracks 1-2) / 2 (barracks 3) / 3 (barracks 4+)
- Лимит бойцов: 3 (guild 1) / 4 (guild 2) / 5 (guild 3+)

**HP и груз:**
- HP: 45 (Т1) / 72 (Т2) / 110 (Т3) + barracks_level × 6
- Груз: 10 (Т1) / 15 (Т2) / 22 (Т3)

### Система рейдов

**Параметры:**
- Окно живости: 36 часов
- Кулдаун повтора: 18 часов
- Защита при успехе: 12 часов

### Система производства

**Станции:**
- Шахта: e, stone, sand, metal
- Ферма: w, cane, wood, wheat
- Завод: f, energy, brick, glass
- Аэроцех: a, bacteria, life, bird

**Духи:**
- Живут 24 часа
- Родная стихия: x3 буст
- Чужая стихия: x2 буст
- Ручной труд: x1

### Система наследия

**Параметры:**
- Лимит фрагментов в день: 2
- Цена установки: 50 энергии
- Стенды: 1-5 (зависит от heritage_center)

### Туториал

7 фаз обучения:
1. Пробуждение — активация кристалла базы
2. Синтез — 13 рецептов для изучения
3. Биом — создание первого микробиома
4. Экспедиция — 2 похода (Вулкан, Горы)
5. Стройка — 4 здания (Склад, Лаборатория, Производство, Казармы)
6. Боевая алхимия — 7 боевых рецептов
7. Завершение — переход к полноценной игре

### Система миров

**Слои биома:**
- Foundation (основа): stone_root, lava_bed, metal_frame
- Medium (среда): swamp_basin, steam_front, sealed_dome
- Energy (энергия): ember_grid, spark_mesh, spore_cycle
- Life (жизнь): moss_bloom, bacteria_hive, spirit_garden

**Архетипы:**
- Опора (nursery): +support, -strain
- Промышленный (forge): +mine_yield, +factory_yield
- Добывающий (wildfire): +volcano_bonus
- Экспедиционный (expedition): +global_loot_bonus, +rare_find_bonus
- Универсальный (patchwork): +farm_yield

**Фокусы:**
- Tech: +factory_yield, +cave_bonus, +global_loot_bonus
- Lore: +rare_find_bonus
- War: +mountains_bonus, +volcano_bonus, +global_loot_bonus
- Wild: +river_bonus, +farm_yield, +aero_yield

---

## Feature Flags

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

---

## Особенности архитектуры

### Сильные стороны
- Чёткое разделение по handlers
- MongoDB как основное persistent storage
- Middleware слой (Auth → Logger → FeatureAccess)
- Feature gating через панель модулей
- Fallback-цепочки для AI провайдеров
- Healthcheck для cloud hosting
- Глубокая игровая система Алхимии с множеством механик

### Риски
- `ai_logic.py` перегружен обязанностями
- Зависимость от порядка routers
- Минимум автотестов
- Глобальные singleton-зависимости (`bot`, `db`)
- Часть поведения держится на широких `try/except`

---

## Middleware Chain

Порядок message middleware:
1. `AuthMiddleware`
2. `ChatLoggerMiddleware`
3. `FeatureAccessMiddleware`

Для callback queries подключён `FeatureAccessMiddleware`.

---

## Runtime Architecture

### Entry Point

Основной процесс запускается через `main.py`.

На старте приложение:
1. Настраивает логирование
2. Вызывает `db.setup()`
3. Инициализирует lore и personas
4. Регистрирует команды Telegram
5. Собирает `Dispatcher`
6. Подключает middleware
7. Подключает routers в фиксированном порядке
8. Запускает `APScheduler`
9. Поднимает `aiohttp` healthcheck
10. Стартует `dp.start_polling(bot)`

### HTTP Layer

Минимальный healthcheck:
- `GET /` -> `200 OK`

Назначение — liveness probe и совместимость с платформами вроде Hugging Face Spaces.

---

## Critical Router Ordering

Порядок `dp.include_router(...)` в `main.py` функционально важен.

Особенно важно:
- `alc_base.router` подключён раньше, чтобы перехватывать deep link / стартовые команды алхимии
- `ex_logic.router` подключён последним как общий fallback-обработчик

Любая перестановка routers должна считаться потенциально опасным изменением поведения.

---

## State Management

### In-Memory State

Часть игровых данных хранится в оперативной памяти процесса:
- `active_games` в `pole_chudes.py`
- `active_hangman_games` в `viselitsa.py`
- временные сессии выбора музыки

Следствие: рестарт процесса завершает активные игровые сессии.

### Persistent State

Долгоживущее состояние хранится в MongoDB:
- история сообщений
- feature flags
- экономика
- прогресс алхимии
- расписания
- авторизация чатов

---

## Assistant Flow

Основной conversational flow реализован в `handlers/ex_logic.py`.

Высокоуровневый сценарий:
1. Определить, является ли сообщение триггером для Экса
2. Классифицировать намерение
3. При необходимости делегировать в music/search/image/game/report handlers
4. Иначе собрать контекст и сгенерировать ответ модели

---

## Voice Flow

`handlers/voice_rec.py`:
1. Проверяет включён ли `auto_voice` для чата
2. Скачивает voice/video_note
3. Отправляет на распознавание
4. Публикует расшифровку
5. Если распознанный текст начинается с обращения к Эксу, прокидывает его в `handle_ex_request`

---

## Deployment Notes

### Local

Запуск:
```powershell
python main.py
```

### Docker

Сборка и запуск описаны в `README.md`.

### Hosted Environments

В коде учитываются переменные:
- `PORT`
- `SPACE_ID`

Это указывает на использование cloud/container hosting, включая Hugging Face Spaces.

---

## Change Safety Rules

Если нужно править проект без высокого риска регрессии:
1. Не менять порядок routers без причины
2. Не трогать fallback-цепочки моделей одним большим рефакторингом
3. Не менять сразу и handlers, и persistence, и prompts в одном коммите
4. Правки делать малыми патчами с синтаксической проверкой
5. Отдельно тестировать игры, голос, музыку и daily reports

---

## Опасные изменения (Алхимия)

1. Правка `DEFAULT_PLAYER_TEMPLATE`
2. Изменение логики `get_or_create_player()`
3. Изменение ключей полей в `alchemy_players`
4. Изменение экономических коэффициентов без миграции
5. Изменение cron-задач
6. Массовая правка callback prefix

---

## Минимальный чек после правок (Алхимия)

1. Новый игрок проходит intro
2. `/base` открывается
3. `/pohod` работает и уважает кулдаун
4. Склад и постройки читаются без ошибок
5. Можно открыть лабораторию
6. Можно открыть экспедиции
7. Можно открыть рейды/форт
8. Не падает утренний отчёт
9. Не ломается `ensure_default_lore()`

---

## Достижения (Алхимия)

Всего 100+ достижений, включая:
- Прогрессовые (сбор ресурсов, постройки)
- Крафтовые (создание специфических предметов)
- Мировые (создание биомов)
- Квестовые (выполнение заказов)
- Экспедиционные (успешные походы)
- Рейдовые (победы, пленники)
