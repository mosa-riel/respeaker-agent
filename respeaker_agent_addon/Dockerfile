# The build context is this subfolder, which can't reach the app at the repo root, so
# the image clones the repo itself. Public GitHub repo → no auth needed.
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RESPEAKER_CONFIG=/data/config.json \
    RESPEAKER_INGRESS=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
# Bust the Docker layer cache when master changes: this file's content is the latest
# commit, so the clone below re-runs on every new commit instead of reusing a stale
# cached layer (which froze the app code across "updates").
ADD "https://api.github.com/repos/mosa-riel/respeaker-agent/commits/master" /tmp/commit.json
RUN git clone --depth 1 "https://github.com/mosa-riel/respeaker-agent" /app
WORKDIR /app
RUN pip install .
EXPOSE 8099 8731
CMD ["python", "/app/addon-run.py"]
