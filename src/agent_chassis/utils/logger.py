import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_configured = False


def get_logger(name: str) -> logging.Logger:
    """Structured-ish stdout logger."""
    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT))
        root = logging.getLogger("agent_chassis")
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        _configured = True
    return logging.getLogger(name)
