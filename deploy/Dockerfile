# Container-Image fuer den JARVIS-Agenten.
# Ziel: CT mit 3 GB RAM auf einem Intel i5-2520M. Deshalb kein
# CUDA, kein torch, kein Multi-Stage-Wahnsinn.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl fuer den Docker-Healthcheck, ca-certificates fuer TLS zu OpenRouter.
# tini kommt via --init in docker-compose, nicht hier.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency-Schicht separat vom Code: Rebuild nach Code-Aenderung
# installiert nur den Layer-Cache und nicht die ganze Dependency-Kette neu.
COPY pyproject.toml README.md ./
RUN mkdir -p jarvis && touch jarvis/__init__.py \
 && pip install --no-cache-dir . \
 && pip install --no-cache-dir ".[tts]"

COPY jarvis ./jarvis
COPY web ./web

RUN useradd --create-home --uid 1000 jarvis \
 && mkdir -p /data/memory /data/skills /data/audio \
 && chown -R jarvis:jarvis /data /app
USER jarvis

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "jarvis.main"]
