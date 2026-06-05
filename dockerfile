FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/usr/src/app/app

WORKDIR /usr/src/app

RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./

RUN uv sync --no-dev

COPY app/ ./app/

EXPOSE ${PORT:-8000}

CMD ["/bin/sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
