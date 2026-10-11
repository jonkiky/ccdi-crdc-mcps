FROM python:3.12-slim

WORKDIR /app
COPY mcp/ccdc/pyproject.toml /app/mcp/ccdc/pyproject.toml
COPY mcp/ccdc/ccdc_mcp /app/mcp/ccdc/ccdc_mcp
RUN pip install --no-cache-dir /app/mcp/ccdc

EXPOSE 8000
USER 65532:65532
CMD ["uvicorn", "ccdc_mcp.server:app", "--host", "0.0.0.0", "--port", "8000"]
