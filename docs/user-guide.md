# MusicSync user guide

MusicSync's supported interface is the TUI: the interactive terminal application launched with `main_tui.py`. The old `main.py` CLI is unmaintained and may not match current functionality.

## Install and launch on Windows

1. Download the repository using GitHub's **Code > Download ZIP**, then extract it (or clone the repository).
2. Install standard Windows Python 3.11 or newer and FFmpeg. Ensure `python` and `ffmpeg` are available in your terminal.
3. Open PowerShell in the extracted project folder and run:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main_tui.py
```

Run the last command again whenever you want to open MusicSync. Always start it from the same project folder: configuration and the default database are relative to that folder. On first launch, the app creates its configuration and catalog. You do not need to copy an example file or enter Spotify credentials.

## Find your way around

Use the arrow keys to select a row and Enter to open it. Esc backs out of a screen or input flow. The left action panel shows the shortcuts for the current screen; some keys change meaning by screen. Press `/` to find available commands.

| Key | Screen | Purpose |
| --- | --- | --- |
| 1 | Home | Overview and suggested actions |
| 2 | Collections | Import and organize groups of tracks |
| 3 | Media | Browse, filter, and manage tracks |
| 4 | Circuits | Configure rotation and feedback workflows |
| 5 | Exports | Manage exported music folders |
| 6 | Jobs | Inspect background work and failures |
| 7 | Settings | Change library, export, and integration settings |

Navigation shortcuts apply outside text-entry flows. Finish or cancel your input before changing screens.

## Try your first collection without Spotify

1. Open **Collections** with `2`, then press `A`.
2. Choose **Local Folder** and enter the full path to a folder containing MP3 files (the local-folder importer currently supports `.mp3`).
3. Review the preview. Choose **Import All As One Collection** or **Import Immediate Subfolders** as appropriate.
4. Open **Jobs** with `6` to follow the import, then return to Collections and open the new collection.

You can also choose **YouTube/Billboard URL** in the Add Collection menu and enter a supported collection URL. Network imports run as background jobs. Importing a track listing is separate from obtaining its audio files. Use the available matching/review actions, then download matched tracks when needed. The Home screen and `/` command menu expose matching, review, and ready-to-download actions.

## Export a collection

In **Settings**, set `exports.roots` to the base folder where you want exports. Return to Collections, select a collection, and press `E` to start its export flow. Follow the prompts to choose the destination and options. Audio files must be available locally to be exported. Inspect the result in **Exports** and track background work in **Jobs**.

Use a dedicated destination folder. Actions such as clearing or deleting managed exports can remove exported files; read the app's confirmation before accepting.

## Optional Spotify setup

Spotify is disabled by default, including for older configurations without a `spotify.mode` value. Disabling it does not delete existing catalog entries or downloaded audio. Spotify imports and refreshes require it to be enabled and configured.

You must use **your own Spotify developer application**; MusicSync does not supply a shared key. Under Spotify's current development-mode rules, the app owner needs Spotify Premium. Creating a developer app alone does not remove that requirement. If you do not meet Spotify's requirements, leave Spotify disabled and use the other sources.

1. Open the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and create your own application, if eligible.
2. Configure its redirect URI as `http://127.0.0.1:8080`.
3. In MusicSync **Settings**, enter your application's `spotify.client_id` and `spotify.client_secret`.
4. Set `spotify.mode` to `enabled`. Changes take effect when saved.
5. Import a Spotify link. Complete Spotify's browser sign-in and permission flow using your own account when prompted.

For another person using your developer app, Spotify requires their account to be allowlisted. Development-mode apps currently support up to five authenticated users. These restrictions and supported API endpoints can change; consult [Spotify's current quota rules](https://developer.spotify.com/documentation/web-api/concepts/quota-modes).

Keep your client secret and login tokens private. Your real `config.yaml` and `.cache` are ignored by Git. Do not send them to other people, include them in screenshots, or bundle your working project folder as a download. Share the GitHub source instead. Settings currently displays credential text, so configure Spotify privately.

## Common problems

- **Spotify is disabled:** Leave it off for non-Spotify use, or complete the optional setup and enable it in Settings.
- **Spotify setup incomplete:** Enter both real credentials; blank values and example placeholders are rejected.
- **Spotify login works but an import fails:** Check app-owner Premium status, user access, and the job error. Authentication does not guarantee access to every Spotify endpoint or playlist.
- **A download or conversion fails:** Check the Jobs error and verify `ffmpeg -version` works in the same terminal. External services may impose restrictions.
- **A Python module is missing:** Install requirements using the same virtual-environment Python used to launch the app.
- **Audio analysis fails:** Verify the analysis dependencies installed successfully and that the track has a readable local audio file.
- **Startup fails:** Read the log location printed by the launcher. Logs can contain local paths and service details; review them before sharing.

## Local data

By default, `database.db` stores the catalog and `library/` holds managed media. `config.yaml` controls paths and optional credentials; `logs/` contains diagnostic output. These files are excluded from Git. Back up the catalog, configuration, and music separately if you want to preserve your working library. Updating source code does not back up your music.
