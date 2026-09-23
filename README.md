# MusicSync

A Python music library manager with an interactive terminal interface. Bring collections from Spotify, YouTube, Billboard, and local folders into one catalog, review track matches, analyze audio, and manage exports.

## Features

- Import and refresh music collections from multiple sources.
- Match tracks to acquisition candidates and review uncertain matches.
- Organize local media in a SQLite catalog.
- Analyze audio features and build vibe-based collections.
- Manage exports and music circuits, including track feedback.
- Run long operations as background jobs in a Textual terminal interface.

## Getting started

Python 3.11 is recommended. Install FFmpeg and make sure `ffmpeg` is available on your PATH for media processing. The legacy CLI uses Windows-specific input handling; Windows is the primary development platform.

From the project folder, create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item config.example.yaml config.yaml
.venv\Scripts\python main_tui.py
```

Edit `config.yaml` to choose your library, database, and export locations. For Spotify integration, supply your own Spotify application client ID and secret and configure the redirect URI as `http://127.0.0.1:8080`. The application uses a browser sign-in flow for Spotify access.

The original command-line interface is also available:

```powershell
.venv\Scripts\python main.py
```

Your credentials, authentication cache, music files, database, and logs stay local and are excluded from version control. This repository contains the application source; it does not include a music library.

## Project structure

| Folder | Purpose |
| --- | --- |
| `core/` | Collections, matching, acquisition, analysis, exports, and jobs |
| `infrastructure/` | Configuration, SQLite persistence, and shared models |
| `plugins/` | Spotify, YouTube, Billboard, and local file integrations |
| `tui/` | Textual interface and interactive workflows |
| `cli/` | Original command-line interface |
| `docs/` | Architecture and interface documentation |

See [the architecture overview](docs/overview.md) and [background job documentation](docs/jobs.md) for more detail.
