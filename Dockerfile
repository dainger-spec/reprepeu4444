FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# сюда пишется база SQLite — подключите volume, чтобы данные не пропадали
ENV DB_PATH=/app/data/repbot.db
VOLUME ["/app/data"]

CMD ["python", "main.py"]
