"""Configuration du logging structuré JSON pour Portail Socrate.

En mode development → logs human-readable (standard).
En mode production → logs JSON (une ligne par entrée, parsable par Datadog/Loki/etc.).

Usage : appelé au démarrage dans app/main.py.
"""
import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Formateur qui émet chaque ligne de log comme un objet JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Extra fields passés via logger.info("...", extra={"key": "val"})
        for key, val in record.__dict__.items():
            if key not in (
                "args", "created", "exc_info", "exc_text", "filename",
                "funcName", "id", "levelname", "levelno", "lineno",
                "message", "module", "msecs", "msg", "name", "pathname",
                "process", "processName", "relativeCreated", "stack_info",
                "thread", "threadName", "taskName",
            ) and not key.startswith("_"):
                log_entry[key] = val
        return json.dumps(log_entry, ensure_ascii=False)


def configure_logging(environment: str = "development") -> None:
    """Configure le logging global selon l'environnement."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Supprimer les handlers existants
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if environment == "production":
        handler.setFormatter(JsonFormatter())
    else:
        # Mode humain : timestamp + level + message
        fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
        handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))

    root.addHandler(handler)

    # Réduire le bruit de SQLAlchemy en dev
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.WARNING if environment == "production" else logging.WARNING
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    logging.getLogger(__name__).info(
        "Logging configuré", extra={"environment": environment}
    )
