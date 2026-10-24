FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Install the package itself (proper install — no PYTHONPATH tricks).
COPY src/ ./src/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# API by default; the worker unit overrides with:
#   ["python", "-m", "agent_chassis.worker"]
CMD ["uvicorn", "agent_chassis.api:app", "--host", "0.0.0.0", "--port", "8000"]
