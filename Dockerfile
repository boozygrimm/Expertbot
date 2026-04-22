FROM python:3.12-slim

# Рабочая директория
WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y nodejs ffmpeg

# Копируем весь код
COPY . .

# Открываем порт для HF
EXPOSE 7860

# Запуск
CMD ["python", "main.py"]
