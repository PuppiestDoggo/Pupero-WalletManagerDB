# Transactions service (FastAPI) - Alpine
FROM python:3.11-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime deps for building mariadb connector
RUN apk add --no-cache build-base gcc musl-dev linux-headers libffi-dev mariadb-connector-c-dev python3-dev

WORKDIR /app
# Use repo root as build context; copy from service dir
COPY Pupero-WalletManagerDB/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy app source
COPY Pupero-WalletManagerDB/app /app/app
# .env is provided at runtime; not copied into image

EXPOSE 8003

CMD ["/bin/sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${TRANSACTIONS_PORT:-8003}"]
