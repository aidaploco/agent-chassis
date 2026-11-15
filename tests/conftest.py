import pytest

from agent_chassis.config import ChassisSettings, get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Settings are an lru_cache singleton; tests that monkeypatch env need
    a fresh read. They also must not read the developer's ./.env: a local
    HTTP_BEARER_TOKENS or tracing endpoint would change what the tests
    exercise. Tests that need a setting pass it or set the env var."""
    monkeypatch.setitem(ChassisSettings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
