FROM python:3.13-slim

# Pinned, not :latest: an unpinned tool image makes the build unreproducible
# (CI pins its actions by SHA for the same reason). Bump deliberately.
COPY --from=ghcr.io/astral-sh/uv:0.7.21 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Install the package itself (proper install — no PYTHONPATH tricks).
COPY src/ ./src/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Run unprivileged: role packs mounted at PACKS_DIR execute in this process
# with its full privileges (D20), so those should not be root's. /app stays
# root-owned and read-only to the process.
RUN useradd --system --uid 10001 --no-create-home chassis
USER chassis

EXPOSE 8000

# API by default; the worker unit overrides with:
#   ["python", "-m", "agent_chassis.worker"]
CMD ["uvicorn", "agent_chassis.api:app", "--host", "0.0.0.0", "--port", "8000"]
