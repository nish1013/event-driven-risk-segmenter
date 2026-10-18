FROM python:3.14.7-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY init.py init.py.lock monitor.py monitor.py.lock ./
RUN uv sync --script init.py --frozen && uv sync --script monitor.py --frozen
CMD ["uv", "run", "--frozen", "--script", "init.py"]
