# The build context is this subfolder, which can't reach the app at the repo root, so
# the image clones the repo itself. Public GitHub repo → no auth needed.
FROM python:3.13-slim
ARG REPO=https://github.com/mosa-riel/respeaker-agent
ARG REF=master
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RESPEAKER_CONFIG=/data/config.json \
    RESPEAKER_INGRESS=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$REF" "$REPO" /app
WORKDIR /app
RUN pip install .
EXPOSE 8099 8731
CMD ["python", "/app/addon-run.py"]
