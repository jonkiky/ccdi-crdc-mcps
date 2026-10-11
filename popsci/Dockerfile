FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY popsci_mcp /app/popsci_mcp
RUN pip install --no-cache-dir /app

EXPOSE 8003
CMD ["uvicorn", "popsci_mcp.server:app", "--host", "0.0.0.0", "--port", "8003"]
