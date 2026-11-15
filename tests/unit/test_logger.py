import logging

import pytest

from agent_chassis.utils.logger import get_logger


def test_module_loggers_are_unchanged():
    assert get_logger("agent_chassis.worker").name == "agent_chassis.worker"
    assert get_logger("agent_chassis").name == "agent_chassis"


def test_a_script_run_module_still_logs_at_info(caplog):
    """`python -m agent_chassis.worker` runs the module as "__main__". Its
    logger used to sit outside the configured one, at WARNING with no handler,
    so every INFO line the worker wrote (including "worker started" and the
    manifest-override audit lines) vanished."""
    log = get_logger("__main__")

    assert log.name == "agent_chassis.__main__"
    assert log.getEffectiveLevel() == logging.INFO
    with caplog.at_level(logging.INFO, logger="agent_chassis"):
        log.info("worker started role=%s", "lana")
    assert "worker started role=lana" in caplog.text


@pytest.mark.parametrize("name", ["__main__", "some_script", "agent_chassis.x"])
def test_every_logger_has_the_chassis_handler(name):
    chain = []
    log = get_logger(name)
    while log:
        chain += log.handlers
        log = log.parent
    assert any(isinstance(h, logging.StreamHandler) for h in chain)
