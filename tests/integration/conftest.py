"""Shared fixtures for the integration tests."""

import os
import shutil
import socket
import subprocess
import time
import uuid

import psycopg
import pytest


@pytest.fixture(scope="module")
def postgres_url():
    docker_up = (
        shutil.which("docker") is not None
        and subprocess.run(
            ["docker", "info"], capture_output=True, check=False
        ).returncode
        == 0
    )
    if not docker_up:
        if os.environ.get("CI"):  # never let CI go green by skipping these
            pytest.fail("Docker is required for the Postgres-backed tests in CI")
        pytest.skip("Docker is not running — Postgres-backed tests need a container")
    with socket.socket() as probe:  # a free port, not a fixed one
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    name = f"chassis-pg-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            "POSTGRES_USER=test",
            "-e",
            "POSTGRES_PASSWORD=test",
            "-e",
            "POSTGRES_DB=test",
            "-p",
            f"127.0.0.1:{port}:5432",
            "pgvector/pgvector:pg17",
        ],
        check=True,
        capture_output=True,
    )
    url = f"postgresql://test:test@127.0.0.1:{port}/test"
    try:
        for _ in range(60):
            try:
                psycopg.connect(url, connect_timeout=1).close()
                break
            except Exception:  # noqa: BLE001 - any connect error means not ready yet
                time.sleep(0.5)
        else:
            raise RuntimeError("postgres container never became ready")
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
