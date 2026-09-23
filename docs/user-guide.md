# MusicSync user guide

MusicSync's supported interface is the TUI: the interactive terminal application launched with `main_tui.py`. The old `main.py` CLI is unmaintained and may not match current functionality.

## Contents

- [Concepts and source differences](#the-concepts-before-the-buttons)
- [Install and launch](#install-and-launch-on-windows)
- [Navigation](#find-your-way-around)
- [What each menu is for](#what-each-menu-is-for)
- [First collection](#try-your-first-collection-without-spotify)
- [Review](#review-and-resolving-a-bad-source)
- [Analysis and Vibes](#audio-analysis-and-vibe-collections)
- [Vibes appendix: feature meanings and rule language](vibes.md)
- [Status reference](#status-reference)
- [Love and boo](#love-and-boo-outside-circuits)
- [Circuits: complete walkthrough](circuits.md)
- [Optional Spotify setup](#optional-spotify-setup)
- [Troubleshooting](#common-problems)

## The concepts before the buttons

MusicSync separates **knowing about a song**, **having its audio**, and **putting a copy on a device**. A song can appear in the catalog long before you have a playable file.

| Term | Meaning | Example |
| --- | --- | --- |
| Catalog / Media | MusicSync's records of tracks, their sources, statuses, and local file locations | A title imported from a chart, with no audio yet |
| Library | The managed audio files on your computer | An MP3 under `library/` |
| Collection | A named list of catalog tracks | A YouTube playlist, a local-folder import, or your own selection |
| Group | A way to organize collections in the interface | A group containing several workout collections |
| Match | An identified source from which audio can be acquired | A YouTube video chosen for a title imported from Spotify |
| Review | Your decision about an uncertain or unsuitable match | Choosing the album recording instead of a live performance |
| Download / acquisition | Obtaining a local file, either from a remote source or by copying an existing local file | Fetching audio and converting it to MP3 |
| Export | A copy of local music in a destination folder | A collection copied to a USB stick |
| Circuit | A persistent player queue that keeps pending songs and replaces heard ones | A 25-track rotation for an old MP3 player |
| Job | A background operation | Importing a playlist or copying a refill |

A collection is not itself a folder full of audio. Multiple collections can refer to the same catalog song, so organizing a song into another collection does not inherently require downloading another copy. Grouping collections also does not move their audio files.

### Match versus download: a concrete example

Suppose a chart tells MusicSync that a song is called **Example Song** by **Example Artist**. That gives it a track to look for, but no audio file.

1. **Import** records the title and artist. The track is Discovered.
2. **Match** searches acquisition sources and compares candidates. A strong candidate can be accepted automatically; uncertain results go to Review.
3. **Review**, if needed, lets you choose or supply an appropriate source. Approval makes the track Matched.
4. **Download** gets audio from that source and stores it locally. A successful match alone does not make the song playable offline.
5. **Export or Circulate** copies the local audio onto another folder or device.

Matching is a best-effort identification step. A high confidence score can still pick the wrong recording. Check tracks where the version matters, such as studio versus live, cover versus original, or edited versus full-length.

### The starting point depends on the source

| Source | What import supplies | What happens next |
| --- | --- | --- |
| Spotify | Track metadata from an eligible configured developer app | Match to an acquisition source, review if needed, then download. This does not download audio from Spotify. |
| Billboard | Chart titles and artists | Match, review if needed, then download |
| YouTube | Track metadata plus a known YouTube source | Normally starts Matched; download the audio. You can rematch if the source is unsuitable. |
| Local Folder | Existing MP3 files and their metadata | The import job copies files into MusicSync's library automatically; no YouTube search is needed. Successful copies show as downloaded. |
| Manual Collection | A list you assemble yourself | Existing tracks retain their state. Newly added metadata without an audio source still needs matching. |
| Vibe Collection | Tracks selected from the catalog by saved rules | Refresh recomputes membership. Analysis-based rules need analyzed audio; creating a Vibe does not obtain new audio. |

These are typical starting states for newly discovered songs. A track already known to MusicSync may retain an existing match or local file. Network availability, private content, and source restrictions can affect imports and downloads.

**Refresh is not Download.** Refresh asks a source for its current track list, or reruns a Vibe's rules. Download obtains audio for tracks with an acquisition source. The initial Local Folder import also copies audio into the library. A later Refresh updates the listing; newly listed tracks may still need Download to copy their local files.

## Install and launch on Windows

1. Download the repository using GitHub's **Code > Download ZIP**, then extract it (or clone the repository).
2. Install standard Windows Python 3.11 or newer and FFmpeg. For FFmpeg, run `winget install --id Gyan.FFmpeg --exact --source winget`, close the terminal completely, and reopen it. Check `python --version`, `ffmpeg -version`, and `ffprobe -version` before continuing.
3. Open the extracted folder in File Explorer. **Keep opening folders until you see both `requirements.txt` and `main_tui.py`.** Windows may put a `MusicSync-main` folder inside an outer folder also named `MusicSync-main`. The outer folder is not the project folder.
4. In the folder containing those files, click File Explorer's address bar, type `powershell`, and press Enter.
5. Confirm that PowerShell is in the correct folder:

```powershell
Get-Item requirements.txt, main_tui.py
```

Both files should be listed without an error. For example, if you extracted into `Downloads\MusicSync-main` and it contains another `MusicSync-main`, the project folder is `Downloads\MusicSync-main\MusicSync-main`. From the outer folder, run `cd .\MusicSync-main` to enter it.

6. Only after confirming the files are present, run:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main_tui.py
```

The filename is `main_tui.py`, with an underscore and no backslash before it. Copy commands directly from the code block.

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

## What each menu is for

### Home: decide what needs attention

Use Home when you want an overview rather than a particular song. It summarizes the library and offers actions for work such as matching discovered tracks, reviewing uncertain matches, and downloading ready tracks. Home is a starting point for moving unfinished tracks toward usable local audio.

### Collections: work with a list of songs

Use Collections when your task concerns a playlist, chart, album-like group, or selection. Press `A` to choose how to create/import one. Open a collection to see its tracks; use the action panel for refresh, download, history, export, and organization actions.

- **External collections** keep a relationship to a source such as YouTube, Spotify, Billboard, or a local folder. Refresh obtains its current contents.
- **Manual collections** hold tracks you choose. They do not need a remote playlist.
- **Vibe collections** store rules that select tracks from your catalog. Refresh applies those rules again.
- **Groups** organize collections in the list. A group is not a circuit and does not deliver music anywhere.
- **History** shows saved collection revisions/snapshots so you can inspect previous membership. A snapshot is a record of the list, not an audio backup.
- **Archive** hides a collection from the normal active view. It does not mean its songs have been physically erased from your library.

For example, make a manual collection for a road trip, or import an existing playlist and refresh it when its source changes. Choose a circuit only when you want its repeating listening/refill behavior.

### Media: work with individual tracks across the catalog

Use Media to find a song without remembering its collection. It includes tracks without files, not just downloaded music. Filters help isolate discovered, matched, review, failed, downloaded, or archived tracks. Open a track for its metadata and source details; the action panel and `/` menu expose the actions appropriate to its state.

Match searches for an audio source. Rematch searches again when you want a different source. Download acquires the audio. Play needs a usable local file and opens playback through the computer's associated player. Love/Boo records your preference; it does not approve a match.

### Circuits: maintain a queue on a simple player

Use Circuits when you want a small selection to evolve as you listen. A circuit remembers its destination, source pool, target count, and feedback. The central action is **Circulate**: inspect progress, preserve unheard files, and refill.

Read the [full circuit walkthrough](circuits.md) before using this with your player. It explains the missing-file progress marker, the preview, why filenames have prefixes, and how Global/Here love and boo affect refills.

### Exports: deliver a collection to a folder

Use Exports when you want an output folder corresponding to a collection, without the circuit's listening-progress logic. Exporting uses audio already on your computer.

A managed export remembers its destination and collection. Creating one initially creates an empty managed destination; **Update** fills it. Later updates rebuild the output from currently exportable tracks. **Shuffle** makes a randomized output, optionally limited in size. **Clear** empties exported MP3s while retaining the managed export. **Recover** scans configured export roots for MusicSync manifests that can be registered again.

Use a dedicated destination folder: Update, Shuffle, and Clear can delete MP3 files in that output folder. They are not merely additive copying operations. Delete has options concerning the folder itself; read the confirmation. None of these actions should be aimed at your only copy of original music.

Unlike a circuit, an export does not treat deletion of the last-played track as listening progress. For an album you want to keep unchanged on a device, an export is usually the simpler choice.

### Jobs: see what the application is doing

Imports, matching, downloads, analysis, export updates, and circulation can run in the background. Open Jobs to see progress and error details. A queued job has not necessarily started yet. Where offered, pause/cancel actions are cooperative: the current operation may need to reach a stopping point.

Job history is in memory for the current app session. Wait for file-writing jobs to finish before disconnecting a player. If a batch completes but a particular track is still unavailable, inspect that track's state and the job details; completion of the batch is not proof that every source was usable.

### Settings: paths, optional integrations, and processing behavior

Use Settings to choose the managed library location and valid export roots, configure optional Spotify credentials, and adjust matching, YouTube, and audio-analysis behavior. Start with defaults unless you have a reason to change them.

Matching thresholds control how readily a candidate is accepted without review. More automatic acceptance trades manual work for the risk of a wrong recording. Audio-analysis worker profiles control processing concurrency; larger settings are not automatically faster on every computer. YouTube browser-cookie configuration is optional and relevant only when a source needs it.

Changing a path setting is not a migration tool: do not assume existing files or the open database move automatically. Keep the original locations backed up and restart when changing the database path. Spotify credentials are optional and currently visible as text in Settings; do not include them in screenshots.

## Review and resolving a bad source

Review means MusicSync needs a decision about which recording/source to use. It is not a rating of whether you like the music.

1. Use Home's review action, a track's Review action, or the `/` command menu to open the relevant review items.
2. Inspect candidate titles, durations, and other displayed details. Use the screen's current action hints to choose/approve a candidate or supply a source manually.
3. If you cannot choose confidently, skip it and return later rather than approving an unrelated recording.
4. After approval, download the track. Approval selects a source; it does not itself copy the audio.

A source can become unavailable after it was matched. Inspect the failure, retry when appropriate, or rematch to a different recording. A network or conversion error is not automatically evidence that the title/artist match was wrong.

## Audio analysis and Vibe collections

Audio analysis examines local audio and stores measurements used to describe and filter tracks. It is separate from matching and downloading. Run analysis on downloaded tracks using the available analysis actions or `/` menu before relying on audio-based Vibe rules.

A Vibe collection is a saved selection rule, such as warm-toned music with moderate activity. The editor supports tempo, tempo stability, activity, intensity, dynamics, tone, texture, and vocals. These are computed descriptions of sound, not guaranteed genre, mood, or vocal labels.

The rule structure is **all Required Rules AND all rules in at least one normal group**. Allowed values within a single feature rule can be alternatives, such as Warm or Balanced. With no normal groups, the Required Rules can define the collection by themselves. Preview the results before saving, and refresh the Vibe after new music is analyzed or rules change. Unanalyzed tracks cannot satisfy measurements they do not have.

For example, require `Texture is Clean or Light`, then use one group for `Tempo is Relaxed or less` and another for `Activity is Moderate`. The resulting collection can be a circuit source. The Vibe decides what belongs in the pool; the circuit decides what to put on the player next.

For plain-language explanations of every feature, including Warm versus Bright versus Sharp, supported rule syntax, and worked recipes, read the [Vibes appendix](vibes.md).

## Status reference

Statuses describe different things on different screens. A track can be downloaded while an export is Out Of Date and a job is Queued. These statements do not conflict.

### Track availability and matching

| Label / marker | Meaning | Typical next step |
| --- | --- | --- |
| Discovered / `?` | A catalog entry without an accepted acquisition source | Match it |
| Matched / `M` | MusicSync has a source to acquire, but this is not proof of a local file | Download it |
| Review / `R` | A source decision needs your attention, including uncertain or forced rematches | Review candidates or provide a suitable source |
| Match Failed / `M!` | The current source/match is flagged as unusable | Inspect details and retry/rematch as appropriate |
| Downloaded / checkmark | The catalog has a registered local audio path | Play, analyze, export, or include in a circuit |
| Archived / `A` | Hidden from the normal active workflow | Use the archived view and restore if wanted |

A downloaded checkmark reflects the registered file path, not a continuous disk check. If a file is moved or removed outside MusicSync, library verification is needed to reconcile the catalog. Also, the UI can prioritize a local-file checkmark over the underlying matching state.

### Analysis state

| State | Meaning |
| --- | --- |
| Missing / not analyzed | No usable current analysis is available |
| Complete / analyzed | Analysis has been saved successfully |
| Stale | Saved analysis exists, but some measurements use an older analyzer version |
| Failed | Analysis could not finish; inspect the error before retrying |

These concern audio measurements, not whether the song has been matched or loved.

### Background jobs

| State | Meaning |
| --- | --- |
| Queued | Waiting to run, possibly behind other work or dependencies |
| Running | Currently processing |
| Paused | Temporarily stopped at a cooperative pause point |
| Completed | The job finished its work; inspect item details for batch-specific outcomes |
| Failed | The job ended with an error |
| Cancelled | The job was stopped; completed file operations are not automatically undone |

### Exports

| State | Meaning |
| --- | --- |
| Empty | The export was created or cleared; Update or Shuffle can populate it |
| Up To Date | The saved output information agrees with the current exportable collection state |
| Out Of Date | The collection/output information has changed since delivery |
| Shuffled | The last output was a shuffled selection |
| Device Disconnected | The configured destination root is unavailable |
| Missing | The expected output folder is unavailable |
| Missing Manifest | The folder exists but its `.musicsync.json` tracking file is absent |

Up To Date and Shuffled describe recorded export state; they do not certify the integrity of every file after outside edits.

### Circuits

| State / wording | Meaning |
| --- | --- |
| Ready | The circuit has its manifest but no previous tracks to carry; circulate to fill it |
| In Loop | A previous delivery exists and the folder/manifest are present |
| Folder Missing | The configured folder is unavailable; check the player connection and path |
| Needs Manifest | The folder lacks `.musiccircuit.json`; check that you have the intended folder |
| Last heard: none yet | No missing-file progress marker is currently detected |
| Fresh tracks | Pending tracks after the inferred listening cutoff, not globally new discoveries |

See [the circuit preview example](circuits.md#worked-example) for Heard, Missing markers, and Add counts.

## Love and boo outside circuits

`L` loves and `B` boos a track in ordinary Media/collection track lists. These are Global preferences. Pressing the same rating again returns it to neutral. The rating belongs to the catalog track, so it is not a separate vote each time the track appears in another collection.

Love/Boo is independent of Match/Review: you can dislike a perfectly correct recording or love a song whose audio still needs downloading. Neither rating downloads, deletes, or archives the song. Circuit refill uses these preferences; **Global boo reduces selection likelihood rather than banning the track**. To exclude it from a particular circuit's new refills, use that circuit's **Here** boo. Read [the feedback guide](circuits.md#love-boo-and-neutral) for scope and save/cancel behavior.

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
- **Could not open requirements file:** You are probably in the outer extraction folder. Open the folder containing both `requirements.txt` and `main_tui.py`, then rerun the setup commands there. If neither file exists anywhere in the extracted download, download and extract the repository ZIP again. The pip update notice is unrelated to this error.
- **A Python module is missing:** Install requirements using the same virtual-environment Python used to launch the app.
- **Audio analysis fails:** Verify the analysis dependencies installed successfully and that the track has a readable local audio file.
- **Startup fails:** Read the log location printed by the launcher. Logs can contain local paths and service details; review them before sharing.

## Local data

By default, `database.db` stores the catalog and `library/` holds managed media. `config.yaml` controls paths and optional credentials; `logs/` contains diagnostic output. These files are excluded from Git. Back up the catalog, configuration, and music separately if you want to preserve your working library. Updating source code does not back up your music.
