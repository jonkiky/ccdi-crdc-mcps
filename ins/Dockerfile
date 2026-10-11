FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY ins_mcp /app/ins_mcp
RUN pip install --no-cache-dir /app

EXPOSE 8002
CMD ["uvicorn", "ins_mcp.server:app", "--host", "0.0.0.0", "--port", "8002"]
