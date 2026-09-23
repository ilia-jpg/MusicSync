import sys
from pathlib import Path
from infrastructure.logging_config import configure_session_logging
from cli.app import MusicSyncCLI

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent
    configure_session_logging(project_root / "logs")
    try:
        app = MusicSyncCLI()
        app.run()
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user. Shutting down MusicSync.")
        sys.exit(0)
