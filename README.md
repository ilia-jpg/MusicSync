# MusicSync

A Python music library manager with an interactive terminal interface. Bring collections from Spotify, YouTube, Billboard, and local folders into one catalog, review track matches, analyze audio, and manage exports.

## Features

- Import and refresh music collections from multiple sources.
- Match tracks to acquisition candidates and review uncertain matches.
- Organize local media in a SQLite catalog.
- Analyze audio features and build vibe-based collections.
- Manage exports and music circuits, including track feedback.
- Run long operations as background jobs in a Textual terminal interface.

**Use the TUI (`main_tui.py`).** The original CLI (`main.py`) is legacy and unmaintained; it is not the supported way to use MusicSync.

**Spotify is optional and disabled by default.** You can use local folders, YouTube, and Billboard without Spotify credentials.

See the [user manual](docs/user-guide.md) for menu explanations, collections, source-specific import workflows, matching versus downloading, statuses, audio analysis, and setup.

Using an older MP3 player? Start with [Circuits: a listening loop for an old MP3 player](docs/circuits.md), including a worked example and Global/Here love and boo feedback.

## Getting started

Python 3.11 is recommended. Install FFmpeg and make sure `ffmpeg` is available on your PATH for media processing. The legacy CLI uses Windows-specific input handling; Windows is the primary development platform.

After extracting the ZIP, open folders until you can see **both `requirements.txt` and `main_tui.py`**. Windows may create an outer extraction folder containing another `MusicSync-main` folder; open that inner folder too. In File Explorer, click the address bar, type `powershell`, and press Enter to open PowerShell in the correct folder.

Check the location first:

```powershell
Get-Item requirements.txt, main_tui.py
```

Both files must be listed. If either is missing, open the inner project folder before continuing. Then create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main_tui.py
```

On first launch, MusicSync creates `config.yaml` automatically. Use Settings to choose your library and export locations. No Spotify account or API key is needed to start. For optional Spotify setup with your own developer app, see the [user guide](docs/user-guide.md#optional-spotify-setup).

Your credentials, authentication cache, music files, database, and logs stay local and are excluded from version control. This repository contains the application source; it does not include a music library.

## Project structure

| Folder | Purpose |
| --- | --- |
| `core/` | Collections, matching, acquisition, analysis, exports, and jobs |
| `infrastructure/` | Configuration, SQLite persistence, and shared models |
| `plugins/` | Spotify, YouTube, Billboard, and local file integrations |
| `tui/` | Textual interface and interactive workflows |
| `cli/` | Original command-line interface |
| `docs/` | User manual and circuit walkthrough |

See the [user manual](docs/user-guide.md) and [circuit walkthrough](docs/circuits.md) for detailed usage instructions.
