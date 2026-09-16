FROM python:3.11-slim

# ffmpeg с libvpx-vp9 нужен для анимированных эмодзи (WEBM VP9 с альфой).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY bot ./bot
RUN pip install --no-cache-dir .

CMD ["python", "-m", "bot.main"]
