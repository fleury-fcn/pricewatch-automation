# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PRICEWATCH_DATABASE_URL=sqlite:////app/var/pricewatch.db

WORKDIR /app

# Dépendances d'abord : couche mise en cache tant que requirements.txt ne change pas
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-deps .

COPY config ./config
COPY app ./app
COPY .streamlit ./.streamlit
COPY data/demo ./data/demo

RUN useradd --create-home --uid 1000 pricewatch \
    && mkdir -p /app/var /app/reports /app/logs \
    && chown -R pricewatch:pricewatch /app
USER pricewatch

EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

# Par défaut : l'API REST. Voir docker-compose.yml pour le dashboard et le pipeline.
CMD ["pricewatch", "api", "--host", "0.0.0.0", "--port", "8000"]
