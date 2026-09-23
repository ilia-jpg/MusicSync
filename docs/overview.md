# MusicSync Architecture

## Project Goal

MusicSync is a desktop application that synchronizes music discovered from external sources (Spotify, YouTube, etc.) into a local music library and managed exports.

The application is CLI-first but designed so that future interfaces (TUI, GUI) can reuse the same business logic.

---

# Layered Architecture

```text
TUI / CLI
     ↓
Core
     ↓
Infrastructure
```

---

# Core Layer

The core layer contains business logic.

Examples:

- Media management
- Collection management
- Matching
- Downloads
- Export management
- Job management
- Query systems

The core layer must never depend on:

- TUI
- CLI
- GUI
- Textual
- User interface concerns

The core should be usable from scripts, tests, CLI commands, or future interfaces.

---

# Infrastructure Layer

The infrastructure layer contains integrations with external systems.

Examples:

- Spotify API
- YouTube
- yt-dlp
- File system operations
- SQLite persistence

Infrastructure should not contain business decisions.

---

# Interface Layers

Current interfaces:

- CLI
- TUI

Future interfaces:

- GUI

Interfaces should be thin wrappers around core functionality.

Business logic should not be duplicated inside interfaces.

---

# Collections

Collections represent sources of media.

Examples:

- Spotify playlists
- YouTube playlists
- Manual collections

Collections contain MediaItems.

Collection sources are handled through a plugin architecture.

The core should not require modification when adding a new collection source plugin.

---

# Media

MediaItem is the canonical representation of music within the application.

All views eventually operate on MediaItems.

Collection track lists are simply filtered views of MediaItems.

Media Details represents a MediaItem regardless of where it was opened from.

---

# Queries

The query system is the primary mechanism for selecting data.

Examples:

- CollectionQuery
- MediaQuery

Queries should be reusable throughout the application.

Features such as:

- Filtering
- Bulk actions
- Review sessions

should operate on query results rather than custom lists.

---

# Jobs

Long-running operations are executed through JobManager.

Examples:

- Refresh Collection
- Download Collection
- Export Update
- Library Verification

Jobs are:

- Atomic
- In-memory
- Non-persistent
- Background tasks

The TUI submits jobs but does not manage job execution.