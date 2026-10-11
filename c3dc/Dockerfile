FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY c3dc_mcp /app/c3dc_mcp
RUN pip install --no-cache-dir /app

EXPOSE 8001
CMD ["uvicorn", "c3dc_mcp.server:app", "--host", "0.0.0.0", "--port", "8001"]
