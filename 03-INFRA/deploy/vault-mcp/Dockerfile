FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src

RUN pip install --no-cache-dir .

# Which engine commit this image carries. `nexgen deploy vault-mcp` passes it as
# a build argument and the image publishes it on its unauthenticated health
# route, so `nexgen doctor` can compare the deployed half with the engine it is
# looking at instead of guessing. An image built any other way carries "unknown":
# the route then omits the field and the doctor says so, which is the truth.
# Kept below the dependency install so a new commit does not invalidate it.
ARG NEXGEN_ENGINE_COMMIT=unknown
ENV NEXGEN_ENGINE_COMMIT=${NEXGEN_ENGINE_COMMIT}
LABEL org.opencontainers.image.revision=${NEXGEN_ENGINE_COMMIT}

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /vault \
    && chown -R appuser:appuser /app /vault

USER appuser

EXPOSE 8080

CMD ["python", "-m", "vault_mcp_server"]
