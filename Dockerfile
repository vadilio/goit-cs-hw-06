# Используем стабильный Python
FROM python:3.11-slim

WORKDIR /app

# Скопируем все файлы приложения в образ
COPY . /app

# Установим зависимости
RUN pip install --no-cache-dir pymongo

# expose http и socket порты
EXPOSE 3000 5001

# Запустить main.py
CMD ["python", "main.py"]