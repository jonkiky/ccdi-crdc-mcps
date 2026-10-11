FROM python:3.12-slim

WORKDIR /app
COPY mcp/cbioportal/pyproject.toml /app/mcp/cbioportal/pyproject.toml
COPY mcp/cbioportal/cbioportal_mcp /app/mcp/cbioportal/cbioportal_mcp
RUN pip install --no-cache-dir /app/mcp/cbioportal

EXPOSE 8000
USER 65532:65532
CMD ["uvicorn", "cbioportal_mcp.server:app", "--host", "0.0.0.0", "--port", "8000"]
