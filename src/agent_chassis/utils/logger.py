import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_ROOT = "agent_chassis"
_configured = False


def get_logger(name: str) -> logging.Logger:
    """Structured-ish stdout logger."""
    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT))
        root = logging.getLogger(_ROOT)
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        _configured = True
    # A module run as a script (`python -m agent_chassis.worker`) has the name
    # "__main__", which is outside the "agent_chassis" logger that carries the
    # handler and the INFO level: its INFO lines were silently dropped,
    # including "worker started" and the manifest-override audit lines (D11).
    if name != _ROOT and not name.startswith(f"{_ROOT}."):
        name = f"{_ROOT}.{name}"
    return logging.getLogger(name)
