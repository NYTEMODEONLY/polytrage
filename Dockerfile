FROM python:3.14-slim

WORKDIR /app

# Install dependencies first for layer caching
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source
COPY src/ src/
COPY polytrage.example.toml polytrage.toml

# Install the package
RUN pip install --no-cache-dir -e .

# Data volume for logs, heartbeat, trades
VOLUME ["/app/data"]

ENV POLYTRAGE_LOG_FILE=/app/data/polytrage.log
ENV POLYTRAGE_HEARTBEAT_FILE=/app/data/heartbeat.json
ENV POLYTRAGE_TRADES_FILE=/app/data/trades.jsonl

HEALTHCHECK --interval=120s --timeout=5s --start-period=60s --retries=3 \
    CMD ["polytrage", "health"]

ENTRYPOINT ["polytrage"]
CMD ["--headless", "--paper"]
