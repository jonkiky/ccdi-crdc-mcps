FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY ods_content_mcp /app/ods_content_mcp
RUN pip install --no-cache-dir /app \
    && useradd --system --uid 10001 --create-home ods

ENV ODS_CONTENT_DIR=/content
USER 10001
EXPOSE 8003
CMD ["uvicorn", "ods_content_mcp.server:app", "--host", "0.0.0.0", "--port", "8003"]
