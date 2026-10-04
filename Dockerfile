FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fontconfig ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md .env.example ./
COPY clipfactory ./clipfactory
COPY config ./config
COPY assets ./assets
RUN pip install --no-cache-dir -e ".[all]"

# Everything stateful (config overrides, .env, database, clips, tokens) lives in /data
ENV CLIPFACTORY_HOME=/data
VOLUME /data
WORKDIR /data
CMD ["clipfactory", "daemon", "--interval", "300"]
