FROM python:3.12-slim

# ffmpeg/ffprobe нужны студии кружков и голосовых; curl — для HEALTHCHECK.
# nodejs в образ не ставим: бот его не запускает, сборка страниц RAID живёт
# в npm-скриптах репозитория и в CI.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости отдельным слоем: код меняется чаще, чем requirements.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Код. Что именно попадает в образ, ограничивает .dockerignore.
COPY . .

# Процессу бота root не нужен: ему нечего писать в файловую систему, кроме
# временных файлов. Порт 7860 не привилегированный, права на /app хватает.
RUN useradd --create-home --uid 10001 bot && chown -R bot:bot /app
USER bot

ENV PYTHONUNBUFFERED=1 \
    PORT=7860

EXPOSE 7860

# main.py поднимает aiohttp-healthcheck на PORT; Telegram работает по long
# polling, поэтому «жив» = отвечает локальный HTTP.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/" || exit 1

CMD ["python", "main.py"]
