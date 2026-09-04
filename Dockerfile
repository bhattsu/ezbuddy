FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Omit test deps from the production image; CPU torch first to avoid CUDA wheels (~1 GB).
RUN grep -vE '^(pytest|pytest-asyncio)' requirements.txt > requirements-prod.txt \
    && pip install --no-cache-dir --prefix=/install \
        torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir --prefix=/install -r requirements-prod.txt

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        pandoc \
        libglib2.0-0 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser . .

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
