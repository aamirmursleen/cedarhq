FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CEDARHQ_DATABASE_PATH=/data/cedarhq.sqlite3 \
    CEDARHQ_SECURE_COOKIES=1 \
    CEDARHQ_DEMO_MODE=1

WORKDIR /app

COPY app.py /app/app.py
COPY cedarhq /app/cedarhq
COPY migrations /app/migrations
COPY static /app/static
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 3000

CMD ["/app/docker-entrypoint.sh"]
