import logging
import sys
import datetime
from pathlib import Path


DEFAULT_LOG_DIR = Path("logs")


def configure_session_logging(log_path: str | Path = DEFAULT_LOG_DIR, console: bool = True) -> Path:
    """Configure a timestamped per-run log file."""
    resolved_path = _session_log_path(log_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(resolved_path, mode="x", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    logging.getLogger("asyncio").setLevel(logging.WARNING)
    for noisy_logger in (
        "audioread",
        "librosa",
        "llvmlite",
        "numba",
        "numba.core",
        "numba.core.byteflow",
        "numba.core.interpreter",
        "soundfile",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    logging.getLogger(__name__).info("Session logging started: %s", resolved_path)
    return resolved_path


def _session_log_path(log_path: str | Path) -> Path:
    path = Path(log_path)
    if path.suffix:
        directory = path.parent
    else:
        directory = path

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return (directory / f"session-{timestamp}.log").resolve()
