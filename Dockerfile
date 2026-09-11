FROM python:3.13-slim
WORKDIR /app

# zoneinfo for Asia/Kolkata quiet-hours math (slim images strip tzdata)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY simulate/ simulate/
COPY scripts/ scripts/
COPY config.yaml .

# persist state outside the image layer (mount a volume here)
RUN mkdir /app/data \
    && useradd -m -u 10001 agent \
    && chown -R agent /app/data
ENV RECOVERY_DB=/app/data/recovery.db
USER agent

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/report')" || exit 1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
