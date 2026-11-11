# План: Бендер отвечает видеокружками (Telegram video note)

> **Статус: ПЛАН. Реализация не начата.** Документ фиксирует agreed-дизайн, чтобы его
> можно было выполнить по шагам позже (или передать другому исполнителю) без потери
> контекста. Никакого кода фичи в репозитории пока нет.
>
> Дата согласования: 2026-11-11.

## 0. Задача в одну строку

По явной команде пользователя Бендер (`p_id="bender"`) отвечает не только текстом, но и
**кружком** (round video note): собственный робот-аватар, озвученный синтезом речи, с
синхронной артикуляцией рта.

### Рамки (что делаем и чего не делаем)

| Делаем | Не делаем |
| --- | --- |
| Запуск **только по команде** (`/bender …`, «Экс, кружок …») | Автоответ кружком на каждое сообщение |
| Тяжёлый рендер на **второй машине** (InstaBox) | Рендер/TTS на Render |
| Медиа в Telegram грузит **воркер** | Прогон байтов через Render (трафик нерационален) |
| Свой процедурный арт и свой риг | Чужие персонажи из `characters/` (не Apache), образы Futurama/Disney |
| Клонирование **текста** в речь | Клонирование голоса актёра дубляжа |

## 1. Принятые решения (зафиксированы, не менять самовольно)

| # | Решение | Значение | Следствие |
| --- | --- | --- | --- |
| 1 | Триггер | только по команде | реалтайм-рендер приемлем; keepalive не нужен |
| 2 | Хост бота | Render **free** (0.1 CPU / 512 MB / 750 ч / 5 ГБ egress / spin-down 15 мин) | бот — тонкий клиент; кэш обязателен; второй сервис на Render не заводим (750 ч ≈ один сервис) |
| 3 | Рендер-машина | **InstaBox** free (4 vCPU / 8 GiB, Debian 13, `/data` 20 GiB, root 16 GiB, egress 100 GiB/мес, автопауза 30 мин, max-run 480 мин → cooldown) | воркер спит, будим запросом; снапшот после настройки |
| 4 | Аватар | **самосборная модель `.livliv` формата v4** | `meta.watermark` отсутствует → марка LivLiv не рисуется легально |
| 5 | Арт | **процедурная отрисовка на Pillow** | детерминированно, ноль авторских рисков, пересборка одной командой |
| 6 | TTS | **Silero `v5_6_ru`, спикер `eugene`**, SR 24000 | 24 кГц = внутренний `RATE` плеера, ресемплинг не нужен |
| 7 | Тембр | лёгкая роботизация ffmpeg-фильтрами **до** липсинка | рот артикулирует тот звук, который слышен |
| 8 | Доставка медиа | **вариант C′: воркер грузит кружок в приватный канал-склад и возвращает `file_id`; бот отправляет его в чат** | трафик Render на клип ≈ 1 КБ JSON, а не ~3 МБ |
| 9 | Токен бота на коробке | **root-only файл + systemd `LoadCredential=`** | токен не в коде, не в логах, не в URL; ротация через @BotFather за минуту |
| 10 | Фича-флаг | `FEATURES["video_notes"]`, **default `False`** | включается админом через панель модулей чата |

### Почему C′ (канал-склад), а не «воркер пишет в чат»

C′ — это вариант C («склад»), где складом служит **приватный канал**, а не приватная группа:
канал создаётся в два клика, в него не нужно никого добавлять, и `sendVideoNote` каналы
поддерживает явно.

- `file_id` в Telegram **привязан к боту, который загрузил файл**. Если медиа не идёт через
  Render — грузить должен воркер, значит токен бота обязан быть на коробке. Третьего
  варианта нет: чужой «загрузочный» бот не подойдёт, его `file_id` наш бот не примет.
- Официальная дока Bot API для `sendVideoNote`: `chat_id` — «Unique identifier for the target
  chat **or username of the target bot, supergroup, or channel**»; и про сам файл — «Pass a
  `file_id` as String to send a video note that exists on the Telegram servers
  (**recommended**)». Значит заливка в приватный **канал** и отправка по `file_id` в чат —
  штатный, рекомендуемый сценарий.
- **Изоляция.** Воркер знает только id канала-склада и физически не умеет писать
  пользователям. Если приватный порт скомпрометирован, злоумышленник сможет постить только
  в твой канал (это сразу видно), а не рассылать кружки по всем чатам от имени бота.
- **Контроль UX остаётся у бота**: reply, темы форума, удаление статус-сообщения, флаги по
  чатам, фолбэки. Контракт `/render` тоньше (без chat-контекста).
- **Бонус**: канал = бесплатный постоянный кэш всех клипов + наглядный лог для отладки.
- Цена: +1 ход в Telegram (~0.3–1 с) на фоне 15–25 с рендера — незаметно.

## 2. Архитектура

```
Пользователь            Бот (Render free)                Воркер (InstaBox)          Telegram
    │  «/bender привет»      │                                  │                        │
    ├───────────────────────►│                                  │                        │
    │                        │ 1. флаг video_notes, кулдаун,    │                        │
    │                        │    очередь(1)                    │                        │
    │                        │ 2. статус «⏳ …» из persona.loading                        │
    │                        │ 3. send_chat_action(record_video_note) ──────────────────►│
    │                        │ 4. ПАРАЛЛЕЛЬНО:                  │                        │
    │                        │    (a) LLM → текст ≤240 знаков   │                        │
    │                        │    (b) GET /health + Bearer ibp_…┼─── будит машину ──────►│
    │                        │ 5. кэш Mongo: hash → file_id?    │                        │
    │                        │    ЕСТЬ → goto 9 (0 байт, мгновенно)                      │
    │                        │ 6. POST /render {text,mood}      │                        │
    │                        │    + HMAC + Bearer ibp_… ───────►│                        │
    │                        │                                  │ 7. Silero → WAV 24 кГц │
    │                        │                                  │    → роботизация ffmpeg│
    │                        │                                  │    → Playwright+livliv │
    │                        │                                  │    → MediaRecorder webm│
    │                        │                                  │    → ffmpeg H.264/AAC  │
    │                        │                                  │    → thumbnail.jpg     │
    │                        │                                  │ 8. sendVideoNote(КАНАЛ,│
    │                        │                                  │    mp4, thumbnail) ───►│
    │                        │◄── {file_id,duration,length} ────┤                        │
    │                        │ 9. send_video_note(chat, file_id)┼───────────────────────►│
    │◄─────── кружок ────────┤ 10. сохранить file_id в Mongo    │                        │
    │                        │ 11. удалить статус-сообщение     │                        │
```

### Поток по шагам

1. Хендлер ловит команду (`/bender <текст>` или сообщение, начинающееся с «Экс, кружок»).
2. Проверки: фича `video_notes` включена для чата, кулдаун не активен, очередь свободна.
3. Отправляем статус-сообщение (фраза из `persona["loading"]`, как в `handlers/ex_logic.py`)
   и `send_chat_action(record_video_note)`; действие повторяем каждые ~5 с, пока идёт рендер.
4. **Параллельно** (`asyncio.gather`): (а) запрос к LLM за текстом ответа Бендера и
   (б) `GET /health` воркера с токеном приватного порта — этот запрос **будит** автопаузнутую
   машину (5–15 с), так что пробуждение перекрывается работой LLM.
5. Считаем `cache_key = sha256(текст + speaker + модель_плеера + версия_стиля + версия_модели)`.
   Если в Mongo есть `file_id` — сразу шаг 9, **0 байт медиа и 0 секунд рендера**.
6. Иначе `POST /render` с подписанным телом (без `chat_id`!).
7. Воркер: TTS → роботизация → рендер в headless-браузере → транскод в MP4 → миниатюра.
8. Воркер: `sendVideoNote(CHANNEL_ID, mp4, duration, length, thumbnail)` → получает `file_id`.
9. Бот: `send_video_note(chat_id, video_note=file_id, duration=…, length=480)`.
10. Бот: `update_one` в `bender_clips` (сохраняем `file_id`, `duration`, `cache_key`).
11. Бот: удаляет статус-сообщение.

## 3. Безопасность токена бота (root-файл + systemd)

Токен лежит на коробке, поэтому закрываем его в три слоя.

**Слой 1 — хранение.** Файл `/etc/bender/token`, владелец `root:root`, права `0400`,
содержимое — одна строка с токеном. В код, в git, в логи, в URL токен не попадает.

```bash
sudo mkdir -p /etc/bender
sudo install -m 0400 -o root -g root /dev/null /etc/bender/token
sudo tee /etc/bender/token >/dev/null <<< '1234567890:AAA...REDACTED...'
sudo chmod 0400 /etc/bender/token
```

**Слой 2 — выдача процессу.** systemd-юниту отдаём токен через `LoadCredential=`: процесс
видит его как `$CREDENTIALS_DIRECTORY/token`, файл недоступен другим пользователям.

```ini
[Service]
DynamicUser=yes
LoadCredential=telegram_token:/etc/bender/token
Environment=BENDER_TOKEN_PATH=%d/telegram_token
```

Если `DynamicUser` конфликтует с доступом к `/data`, используем обычного пользователя
`bender` и `EnvironmentFile=/etc/bender/token.env` (тот же `chmod 0400`).

**Слой 3 — транспорт и логика.**

- Порт воркера публикуем как **приватный**: `instabox-port publish 8080 --private`.
  Доступ только с `Authorization: Bearer ibp_…`; чужие запросы получают `401` и **машину не
  будят** (по доке InstaBox). Токен порта показывается один раз — сразу кладём в env Render.
- Поверх транспорта — **HMAC-SHA256 подпись тела** общим секретом + `X-Timestamp`
  (отклоняем старше 300 с) → защита от повтора.
- Rate-limit на эндпоинте, очередь рендера на 1 задачу, лимит длины текста (≤ 600 знаков на
  входе, ≤ 240 в ответе LLM), таймауты на каждом шаге.
- **Никаких внешних URL в запросе не принимаем**: на вход только текст, модель локальная →
  SSRF исключён конструктивно.
- Ротация при инциденте: отозвать токен у @BotFather (секунды), обновить
  `/etc/bender/token`, `systemctl restart bender-worker`; токен порта — `--rotate-token`.
- Опционально (если пригодится): InstaBox **Secrets proxy** — «машина видит плейсхолдер,
  прокси подставляет настоящий ключ». Проверить в дашборде раздел Secrets и возможность
  разрешить хост `api.telegram.org`; если работает — это строго лучше файла.

## 4. Состав воркера (новый каталог `video_worker/`)

```
video_worker/
├── server.py            # FastAPI: /health, /render, /version; auth (Bearer + HMAC), очередь(1), rate-limit
├── auth.py              # проверка ibp_-токена и HMAC-подписи, отсев протухших timestamp
├── tts_silero.py        # silero_tts(language='ru', speaker='eugene', sample_rate=24000) → WAV; кэш озвучки по хэшу текста
├── robot_fx.py          # ffmpeg-фильтры лёгкой роботизации тембра (СТРОГО до липсинка)
├── render_browser.py    # Playwright(Python): запуск Chromium, harness-страница, запись, сбор webm
├── render_page/
│   ├── index.html       # harness: canvas 480×480, кнопка-триггер не нужна (автозапуск)
│   ├── render.js        # Player + load(.livliv Blob) + view.head + createSpeech + MediaRecorder
│   └── vendor/@livliv-dev/player/dist/   # из npm-тарбола v0.3.0 (Apache-2.0), раздаём статикой
├── transcode.py         # webm → H.264 yuv420p + AAC, 480×480, ~1.5 Mbps, faststart; thumbnail JPEG ≤320 px
├── telegram_upload.py   # sendVideoNote в канал-склад (multipart + thumbnail) → file_id
├── cache.py             # двухуровневый кэш: /data/clips/<hash>.mp4 + /data/audio/<hash>.wav
├── model_build/         # билдер модели .livliv (см. раздел 5)
│   ├── art.py           # Pillow: корпус, голова, антенна, линзы-глаза, 5 кадров рта-решётки
│   ├── build.py         # model.json + textures → ZIP `.livliv` (stored, model.json первым)
│   └── validate.py      # JSON Schema v4 + правила §8
├── tests/               # юнит-тесты билдера/валидатора/хэшей (без браузера)
├── setup_instabox.sh    # установка зависимостей на коробке (этап 3)
├── bender.service       # systemd-юнит
└── README.md            # инструкция деплоя + диагностика + лицензионные оговорки
```

Ключевые параметры воркера:

- **Плеер**: `new Player(canvas, { idle: true, maxPixelRatio: 1 })`, canvas 480×480,
  `view.head = true` (камера держит голову в кадре), `load(blob)` — модель читается из
  `/data/models/bender.livliv` как Blob (никаких fetch наружу).
- **Речь**: `createSpeech(player, { motion: 1, bounce: true })` → `speech.say(wavArrayBuffer)`;
  стоп записи по `speech.onIdle`. В headless нужен флаг
  `--autoplay-policy=no-user-gesture-required`, иначе `speech.unlock()` не пройдёт без
  пользовательского жеста.
- **Запись**: `MediaRecorder(canvas.captureStream(24))` → webm. Рендер **реалтайм**:
  12-секундный клип пишется ~12 с (плюс накладные) — допустимо именно потому, что триггер
  ручной и очередь на 1.
- **Chromium**: `--headless=new --use-gl=angle --use-angle=swiftshader
  --enable-unsafe-swiftshader --disable-dev-shm-usage --no-sandbox`.
- **Транскод**: H.264, `yuv420p`, 480×480, ~1.5 Mbps, AAC, `-movflags +faststart`, ≤ 60 с,
  файл ≤ 50 МБ (реально 2–3 МБ).
- Одна страница на задачу; после рендера `player.dispose({ release: true })` и закрытие
  контекста (лимит браузера ~16 WebGL-контекстов).

## 5. Самосборная модель `.livliv` (формат v4)

Пакет — обычный ZIP с расширением `.livliv` (media type `application/vnd.livliv.model+zip`):
`model.json` **первым**, картинки и `model.json` **stored (method 0)**, `keyforms.json` можно
deflate; имена UTF-8 с `/`, без `..` и абсолютных путей. Схема строга
(`additionalProperties: false` везде, кроме `x-*`), поэтому валидация обязательна.

Обязательные поля `model.json`:

| Поле | Значение у нас |
| --- | --- |
| `format` | `"livliv-model"` |
| `version` | `4` |
| `name` | `"Bender-style bot"` (≤ 64) |
| `meta.generator` | `{ "name": "expertbot-model-build", "build": "<git sha>" }` |
| `meta.author` | наш ник/ссылка |
| `meta.license` | наша (`id` SPDX или `LicenseRef-…`) |
| `meta.watermark` | **не задаём** (или `"none"`) → марка не рисуется; по доке плеер «never adds the LivLiv mark to a package that does not ask for it» |
| `canvas` | `[1024, 1024]` (допустимо 16–8192) |
| `files.avatar` | квадрат ~96 px — на миниатюру/кнопку |
| `anchors` | `head{cx,cy,rx,ry}`, `face[x0,y0,x1,y1]`, `neck_y`, `top_y`, `bottom_y`, `hair_root_y` |
| `layers` | см. ниже |
| `expressions` | `[]` на старте |
| `profile` / `tuning` / `rig` | **не задаём** → дефолты плеера |

Слои (процедурный арт на Pillow, детерминированный):

| Слой | `group` / `role` | Заметки |
| --- | --- | --- |
| корпус, голова, антенна | `body` / `head`, role `none` | `depth` для параллакса |
| белки-линзы | `eye_L`, `eye_R` | обязательны `mask` (lossless) и `pivot` |
| веки/затворы | `lid_L`, `lid_R` | рисуются **под** глазами по порядку слоёв |
| зрачки/блики | `iris_L`, `iris_R`, `shine_L`, `shine_R` | `clip` + `range` + `pivot` |
| рот-решётка | `mouth` | `frames` + `visemes` **одинаковой длины**, минимум 3 кадра; берём 5: `closed, half, open, round, wide` |

Волос нет → `hair_root_y` = верх головы (поле обязательное, заполняем из своей геометрии).
`L`/`R` — стороны **изображения**, не персонажа.

Валидация (`model_build/validate.py`, гоняется в тестах и в CI-подобном скрипте):

1. JSON Schema `schema/livliv-model-v4.json` из репозитория `livliv-dev/player`.
2. Правила §8: уникальные `id` и `hotkey`; `clip`/`bones` ссылаются на существующие id;
   все ключи `blush/show/hide/bias` — id из `expressions`; каждый файл из `model.json`
   реально лежит в ZIP; каждое изображение слоя **ровно** `w×h` px, straight alpha, бокс
   внутри холста; нестандартные id выражений начинаются с `x_`.
3. Лимиты читателя: `model.json` ≤ 4 MB, холст ≤ 8192 px, ≤ 512 слоёв, ≤ 2048 записей ZIP,
   ≤ 256 MB распакованного, бюджет вершин решётки 2 097 152.

## 6. TTS и «роботизация»

- `pip install silero` → `from silero import silero_tts`; **не путать** с PyPI-пакетом
  `silero-tts` (0.0.5) — это сторонняя обёртка `daswer123/silero-tts-enhanced`.
- Модель `v5_6_ru`, спикер `eugene`, `sample_rate=24000` (совпадает с `RATE` speech-рантайма
  плеера: огибающая 100 fps, полоса 150–1500 Гц, опережение 30 мс).
- V5 сам ставит ударения, разбирает гомографы, вопросы и эмфазу → текст от LLM отдаём как
  есть, без ручной разметки. Поддерживается SSML — теги сверяем по вики Silero, не выдумывая.
- Требования: PyTorch-система и **AVX2** на x86/64. Ставим **CPU-wheel**:
  `pip install torch --index-url https://download.pytorch.org/whl/cpu` (иначе гигабайты CUDA;
  ~1 ГБ диска из 16 ГБ root). ONNX-варианта сейчас нет: в дереве `snakers4/silero-models`
  onnx-файлов не найдено.
- Роботизация — ffmpeg-фильтры **до** `speech.say()`: лёгкий сдвиг формант / мягкий ring-mod,
  без агрессии, с сохранением разборчивости и огибающей. Иначе рот будет артикулировать не
  тот звук, который слышен.
- Кэш озвучки по хэшу текста в `/data/audio/` — повторный рендер того же текста без TTS.

## 7. Кэш и Mongo

Двухуровневый кэш, потому что диск Render **эфемерный**, а Mongo — нет.

- **Уровень 1 (Render → Mongo Atlas)**, коллекция `bender_clips`:

  ```js
  {
    _id: ObjectId,
    cache_key: "sha256:…",      // текст + speaker + модель плеера + версия стиля + версия модели
    file_id: "DQACAgIAA…",         // видео-кружок, загруженный нашим ботом
    duration: 12,
    length: 480,
    created_at: ISODate,
    hits: 3
  }
  // unique index: cache_key
  ```

  Отправка по `file_id` **не расходует трафик Render** (файл уже на серверах Telegram).
- **Уровень 2 (коробка → `/data`)**: `clips/<hash>.mp4`, `audio/<hash>.wav`, `models/bender.livliv`.
  Если `file_id` вдруг станет невалидным, воркер перезальёт готовый mp4 **без рендера**.
- Посты в канале-складе **не удаляем**: удаление может сделать `file_id` невалидным, а хранение
  бесплатное.

## 8. Изменения в боте (карта файлов)

| Файл | Что добавляем |
| --- | --- |
| `models.py` | `RENDER_WORKER_URL`, `RENDER_WORKER_TOKEN`, `RENDER_WORKER_SECRET`, `BENDER_TTS_SPEAKER` (`eugene`), `BENDER_TTS_MODEL` (`v5_6_ru`), `BENDER_CLIP_LENGTH` (480) |
| `handlers/bender_clip.py` | **новый**: хендлеры команды, wake-ping, очередь(1), кулдаун, HMAC-клиент, работа с кэшем, `send_video_note`, фолбэки, удаление статус-сообщения |
| `handlers/admin_panel.py` | `FEATURES["video_notes"]` = `{label: "Кружки Бендера", icon: "🎥", default: False, hint: "Видеокружки от Бендера по команде."}` |
| `bot_commands.py` | `("bender", "Кружок от Бендера")` в `GROUP_COMMAND_SPECS` |
| `app_setup.py` | регистрация роутера; при необходимости фоновая очередь |
| `database.py` | доступ к коллекции `bender_clips` |
| `tests/` | юнит-тесты: хэш кэша, подпись HMAC, ветвление фолбэков, регистрация хендлера |

`requirements.txt` бота **не тяжелеет**: ни torch, ни Playwright, ни ffmpeg-обвязки.
(ffmpeg в Docker-образ бота уже ставится существующим `Dockerfile` — для кружков он не
понадобится, но и мешать не будет.)

## 9. API-контракт

Заголовки всех запросов: `Authorization: Bearer ibp_…` (гейтвей InstaBox),
`X-Signature: <hmac-sha256(body, secret)>`, `X-Timestamp: <unix>`.

```http
GET /health        → 200 {"ok": true, "version": "…", "queue": 0}   # также будит машину

POST /render
{ "text": "…", "mood": "smirk", "speaker": "eugene", "style_version": 3 }
→ 202/200 { "file_id": "DQAC…", "duration": 12, "length": 480, "cached": false }
→ 4xx/5xx { "error": "…", "code": "queue_busy" | "tts_failed" | "no_webgl"
                        | "context_lost" | "transcode_failed" | "upload_failed"
                        | "too_long" | "bad_signature" }
```

`chat_id` в контракте **нет намеренно**: воркер не умеет писать пользователям.

## 10. Фолбэки и деградация

Цепочка: **кружок → голосовое → текст**. Текст не теряется никогда — он уже сгенерирован на
Render к моменту обращения к воркеру.

> «План Б» ниже — запасной **способ рендера** (PNGTuber вместо livliv), а не вариант доставки
> медиа B; доставка остаётся C′ при любом способе рендера.

| Состояние машины / ошибка | Симптом | Что делает бот |
| --- | --- | --- |
| Работает | `200`, ~18–25 с | кружок |
| Автопауза (30 мин) | запрос ждёт resume (< 1 с + пробуждение) | wake-ping заранее, параллельно LLM; ретрай с backoff |
| Cooldown после 480 мин | `409 machine_cooldown` + `Retry-After` | читаем `Retry-After`, шлём текст + подсказку админу |
| Остановлена вручную | `503` | текст + подсказка админу |
| Свежая загрузка | connection refused / `502` | systemd поднимет сервис; ретрай 2–3 раза |
| Неверный токен порта | `401` (машину не будит) | текст + алерт админу |
| WebGL2 не завёлся | `no-webgl`, `context-lost` | перезапуск страницы → **план Б PNGTuber** (`BodyPlayer` + `PngBody`, марку тоже не рисует, контракт `/render` не меняется) → аудио |
| TTS/транскод упал | `tts_failed`, `transcode_failed` | аудио-фолбэк (воркер грузит OGG/Opus в канал и отдаёт `file_id` для `send_voice`) → текст |
| Заливка в канал не прошла | `upload_failed` | текст |
| `file_id` из кэша протух | `Bad file id` от Telegram | удаляем запись, полный рендер заново (mp4 возьмём с `/data`) |

Латентность: холодный старт ~25–35 с, тёплый ~18–25 с, попадание в кэш — мгновенно.
UX-опция на потом: «в два шага» — сначала голосовое (~5 с), следом кружок.

## 11. Пошаговое развёртывание

### Этап 0. Диагностика коробки (блокер для Silero)

```bash
nproc; free -m; df -h /data
lscpu | grep -o avx2                 # ПУСТО → Silero не взлетит, меняем план TTS
ffmpeg -version | head -1
python3 -V
which chromium chromium-browser
systemctl --version | head -1
```

Проверяем WebGL2 в headless (страница с `canvas.getContext('webgl2')`) и визуально —
`example/demo.html` из репозитория плеера через VNC коробки. Если WebGL2 не заводится →
сразу план Б (PNGTuber).

### Этап 1. Каркас воркера

`video_worker/server.py` + `auth.py` + `/health`, systemd-юнит, публикация приватного порта.
Проверяем `curl -H "Authorization: Bearer ibp_…" https://8080-<id>.<domain>/health`
(401 без токена, 200 с токеном).

### Этап 2. Билдер модели `.livliv` (можно целиком в песочнице)

`model_build/art.py` → `build.py` → `validate.py`, тесты по схеме и правилам §8.
Артефакт: `bender.livliv`, кладём в `/data/models/`. Визуальная проверка — только на коробке
через её VNC (в песочнице браузера нет).

### Этап 3. Зависимости на коробке

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg
uv venv /opt/bender/.venv            # или python3 -m venv
. /opt/bender/.venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install silero fastapi uvicorn playwright numpy
playwright install chromium          # или системный Chromium через executable_path
```

### Этап 4. TTS + рендер + транскод + загрузка в канал

Прогон от начала до конца: текст → WAV 24 кГц → роботизация → webm → mp4 480×480 + thumbnail →
`sendVideoNote(CHANNEL_ID, …)` → `file_id`. Кэши в `/data`.

### Этап 5. Интеграция в бота

`handlers/bender_clip.py`, `FEATURES["video_notes"]` (default False), команда, кэш
`bender_clips`, статус-сообщение, `send_chat_action`, фолбэки, тесты.

### Этап 6. Надёжность и снапшот

Таймауты, ретраи с backoff, очередь(1), кулдаун на чат, алерты админу, метрики в лог.
Затем **снапшот настроенной машины** (лимит 5 снапшотов) — чтобы после сбоя платформы
подняться за минуты, а не переустанавливать всё.

### Настройка Telegram (разово, руками)

1. Создать **приватный канал** (например «Bender clips storage»).
2. Добавить бота администратором с правом постить сообщения.
3. Узнать id канала (например через `getUpdates` после первого поста) → в env воркера.
4. Токен бота → `/etc/bender/token` (раздел 3).
5. В env Render: `RENDER_WORKER_URL`, `RENDER_WORKER_TOKEN`, `RENDER_WORKER_SECRET`.
   **Секреты в чат не присылаем.**

## 12. Критерии приёмки

- [ ] Команда `/bender <текст>` в чате с включённой фичей даёт кружок 480×480 ≤ 60 с.
- [ ] Рот синхронен со звуком (проверяем на 3–4 разных фразах, включая вопросы и числа).
- [ ] Марки LivLiv на видео нет; `meta.watermark` в пакете отсутствует.
- [ ] Повтор той же фразы отдаётся из кэша за < 2 с и без обращения к воркеру.
- [ ] Трафик Render на один уникальный клип — единицы килобайт (проверяем по метрикам Render).
- [ ] При выключенной машине / cooldown / 401 пользователь получает текстовый ответ,
      ошибки нет, админ видит причину в логе.
- [ ] Токен бота отсутствует в коде, в git-истории и в логах воркера.
- [ ] Запрос к `/render` без валидной подписи отклоняется (401/403), машина при этом не будится.
- [ ] Тесты бота и `model_build` проходят локально без браузера и без сети.

## 13. Риски

1. **Лицензия Silero.** `v5_6_ru` — CC BY-NC-SA 4.0: только некоммерческое использование +
   атрибуция. При монетизации — переход на MIT-ветку `v5_cis_base(_nostress)` (спикеры с
   префиксом `ru_`, **без** автоударений: понадобится автопростановка ударений через словарь
   `pymorphy3`, гомографы останутся лотереей). Переключатель держим в env
   (`BENDER_TTS_MODEL`, `BENDER_TTS_SPEAKER`) — смена ветки в одну строку.
2. **AVX2 обязателен** для Silero (этап 0 — блокер).
3. **torch ≈ 1 ГБ** на диске коробки (root 16 ГБ) — ставим строго CPU-wheel.
4. **InstaBox — бета** (YC/InsForge): shared NAT, условия могут меняться, в доках прямо
   сказано «не для 24/7-сервисов», встречаются жалобы «Too many connections». Поэтому
   keepalive не ставим и держим полную цепочку фолбэков.
5. **Реалтайм-рендер**: клип пишется столько же, сколько длится. Без очереди(1) и кулдауна
   упрёмся в max-run 480 мин и cooldown.
6. **Render free**: spin-down 15 мин, 5 ГБ egress, эфемерный диск → весь кэш в Mongo
   (`file_id`) и на коробке (`/data`); второй сервис на Render не заводим (750 ч/мес).
7. **Строгая схема формата v4**: опечатка в поле ломает загрузку → валидация в тестах.
8. **Headless WebGL2** может не завестись → план Б PNGTuber (контракт не меняется).
9. **IP**: образ Бендера — собственность Futurama/Disney. Делаем «своего робота по мотивам»
   (цилиндр-голова, антенна, линзы-глаза, решётка-рот), персонажей из `characters/` не
   используем (они не Apache, а personal non-commercial), голос актёра дубляжа не клонируем.

## 14. Переменные окружения

**Render (бот):**

| Переменная | Значение |
| --- | --- |
| `RENDER_WORKER_URL` | `https://8080-<machine-id>.<domain>` |
| `RENDER_WORKER_TOKEN` | `ibp_…` (приватный порт, показывается один раз) |
| `RENDER_WORKER_SECRET` | общий секрет для HMAC |
| `BENDER_TTS_MODEL` | `v5_6_ru` |
| `BENDER_TTS_SPEAKER` | `eugene` |
| `BENDER_CLIP_LENGTH` | `480` |

**Коробка (воркер):** `BENDER_TOKEN_PATH` (из `LoadCredential`), `BENDER_CHANNEL_ID`,
`BENDER_SHARED_SECRET`, `BENDER_MODEL_PATH=/data/models/bender.livliv`,
`BENDER_DATA_DIR=/data/bender`, `PORT=8080`.

## 15. Чего не делать (проверено на доках и источниках)

- **Не** гонять медиа через Render: 5 ГБ/мес при ~3 МБ на клип = ~1600 клипов, и это
  нерационально, когда `file_id` решает задачу за ~1 КБ.
- **Не** ставить keepalive-пинг на InstaBox: упрёт машину в лимит 480 мин и cooldown.
  Нужный режим — машина спит, наш запрос с токеном её будит.
- **Не** удалять посты из канала-склада (`file_id` может стать невалидным).
- **Не** передавать `thumbnail` при отправке по `file_id`: по доке миниатюра работает только
  с multipart-загрузкой и «Thumbnails can't be reused» → генерим и прикладываем один раз,
  на воркере, в момент заливки в канал.
- **Не** использовать `pip install silero-tts` (сторонняя обёртка), только `pip install silero`.
- **Не** обрабатывать голос роботизацией после липсинка.
- **Не** рассчитывать на ONNX-путь Silero (в дереве моделей onnx-файлов нет).
- **Не** добавлять в `requirements.txt` бота torch/Playwright/ffmpeg-обвязку.
- **Не** принимать в `/render` внешние URL (SSRF).
- **Не** рассчитывать на `fetch_page` для доков InstaBox через их прокси — отдаёт
  `SignatureDoesNotMatch`; пользоваться CLI внутри коробки и дашбордом.

## 16. Открытые вопросы (не блокируют старт)

1. `meta.author` и `meta.license` пакета модели: какой ник и под какой лицензией выпускаем
   своего робота (CC-BY-4.0 / `LicenseRef-ExpertBot` / all rights reserved).
2. Внешность робота: палитра, один большой глаз-линза или два, форма решётки-рта.
   Могу предложить 2–3 варианта на выбор на этапе 2.
3. Нужен ли аудио-фолбэк (голосовое) или достаточно текстового.
4. Есть ли в дашборде InstaBox раздел Secrets с поддержкой `api.telegram.org` — если да,
   можно усилить слой хранения токена (раздел 3).
