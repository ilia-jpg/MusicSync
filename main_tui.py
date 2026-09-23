import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.logging_config import configure_session_logging


if __name__ == "__main__":
    log_path = PROJECT_ROOT / "logs"
    try:
        print("Starting MusicSync TUI...", flush=True)
        log_path = configure_session_logging(log_path, console=False)
        print("Loading interface modules...", flush=True)
        from tui.app import MusicSyncApp

        print("Opening catalog...", flush=True)
        app = MusicSyncApp()
        print("Launching interface...", flush=True)
        app.run()
    except Exception as exc:
        try:
            import logging

            logging.getLogger(__name__).exception("MusicSync TUI failed during startup.")
        except Exception:
            pass
        print("\nMusicSync failed to start.")
        print(f"{type(exc).__name__}: {exc}")
        print(f"\nDetails were written to: {log_path}")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        sys.exit(1)
