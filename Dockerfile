# One image runs both services (main app and fake counterparties);
# docker compose picks the command.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="fxlab"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so this layer is cached until pyproject.toml changes.
COPY pyproject.toml README.md ./
COPY fxlab ./fxlab
RUN pip install .

# Never run as root inside the container.
RUN useradd --system --uid 10001 --no-create-home fxlab
USER fxlab

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# --proxy-headers: trust X-Forwarded-* from the reverse proxy in front of us (nginx).
CMD ["uvicorn", "fxlab.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
