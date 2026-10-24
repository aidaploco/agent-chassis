import pytest

from agent_chassis.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Settings are an lru_cache singleton; tests that monkeypatch env need
    a fresh read."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
