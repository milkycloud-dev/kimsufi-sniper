FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATE_PATH=/app/data/state.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

RUN useradd --uid 10010 --no-create-home --shell /usr/sbin/nologin sniper \
    && mkdir -p /app/data \
    && chown sniper:sniper /app/data

USER 10010:10010
VOLUME ["/app/data"]

CMD ["python", "main.py"]
