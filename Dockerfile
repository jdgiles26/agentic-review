FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REVIEW_DB=/data/reviews.db

WORKDIR /app

# Source must be present before install: hatchling builds the listed packages.
COPY pyproject.toml README.md ./
COPY agents ./agents
COPY api ./api
COPY cli ./cli
COPY graphs ./graphs
COPY memory ./memory
COPY schemas ./schemas
COPY tools ./tools
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /data && chown app:app /data

USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
