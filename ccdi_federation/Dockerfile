FROM python:3.12-slim

WORKDIR /app
COPY mcp/pyproject.toml /app/mcp/pyproject.toml
COPY mcp/ccdi_federation_mcp /app/mcp/ccdi_federation_mcp
COPY skills/ccdi-federation-ai-copilot/references /app/skills/ccdi-federation-ai-copilot/references
RUN pip install --no-cache-dir /app/mcp

ENV CCDI_REFERENCE_DIR=/app/skills/ccdi-federation-ai-copilot/references
EXPOSE 8000
USER 65532:65532
CMD ["uvicorn", "ccdi_federation_mcp.server:app", "--host", "0.0.0.0", "--port", "8000"]
