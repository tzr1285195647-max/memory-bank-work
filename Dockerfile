FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py run.py worker.py ./
COPY memory_bank ./memory_bank
COPY alembic.ini ./
COPY alembic ./alembic

RUN addgroup --system memorybank \
    && adduser --system --ingroup memorybank memorybank \
    && mkdir -p /data/objects \
    && chown -R memorybank:memorybank /app /data

USER memorybank

EXPOSE 8765

HEALTHCHECK --interval=20s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2)"]

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
