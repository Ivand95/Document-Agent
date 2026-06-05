FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1

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

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
