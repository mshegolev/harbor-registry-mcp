FROM python:3.12-slim

RUN pip install --no-cache-dir harbor-registry-mcp

ENTRYPOINT ["harbor-registry-mcp"]
