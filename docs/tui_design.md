# MusicSync TUI Master Specification

This document defines the intended long-term behavior of the MusicSync TUI.

It is the authoritative reference for future development.

When implementation details conflict with this document, this document should be considered the source of truth unless explicitly superseded.

---

# Philosophy

The MusicSync TUI is:

- Keyboard first
- Fast for large libraries
- Consistent
- Minimal clutter
- Readable

The user should never require a mouse.

All functionality must remain accessible through keyboard navigation.

---

# Global Layout

The application consists of:

```text
Sidebar
Main Content
Status Bar
```

---

# Sidebar

The Sidebar is informational only.

The Sidebar is never focusable.

The Main Content always owns keyboard input.

The Sidebar displays:

```text
1 Home
2 Collections
3 Media
4 Exports
5 Jobs
6 Settings
```

followed by:

```text
──────────────
```

followed by contextual actions.

The active screen remains highlighted.

This indicates location, not focus.

---

# Global Navigation

Screen switching:

```text
1 Home
2 Collections
3 Media
4 Exports
5 Jobs
6 Settings
```

works from anywhere.

---

# Standard Navigation

```text
↑ ↓     Navigate

Enter   Open

Esc     Back
```

---

# Status Bar

The Status Bar displays:

- Success messages
- Error messages
- Informational messages
- Confirmation prompts

Examples:

```text
Export Created
```

```text
Download Failed
```

```text
Delete Export? (Y/N)
```

When confirmation is active:

- Context actions disappear
- User must respond first

---

# Home

Purpose:

Dashboard

Displays:

- Library statistics
- Collection statistics
- Export statistics
- Job summaries

Refreshing occurs when entering Home.

---

# Collections

Displays:

```text
Collection Name
Item Count
```

The list is scrollable.

---

# Collection Details

Navigation:

```text
Collections
    ↓ Enter
Collection Details
```

Displays:

- Collection metadata
- Track list

The track list is the primary content.

Metadata should remain compact.

Example:

```text
Road Trip Playlist
Spotify Playlist

138 Items
Last Refresh: 2026-04-27
Filter: Default

✓ Numb
✓ In The End
R Unknown Song
```

No separator line is required between metadata and track list.

A single blank line is sufficient.

---

# Collection Details Actions

```text
Esc Back

T Filter
J Jump
F Find

V Review
X Export
R Refresh
```

---

# Collection Track List

Displays:

```text
Status
Title
Artist
```

Status icons:

```text
✓ Downloaded
M Matched
R Review
D Discovered
```

Only the symbol is shown.

Status icons should be highly visible.

---

# Media

Displays:

```text
Status
Title
Artist
```

for all media items.

No separator line is required between header and list.

A single blank line is sufficient.

---

# Media Details

Navigation:

```text
Media
    ↓ Enter
Media Details
```

and:

```text
Collection Details
    ↓ Enter
Media Details
```

Media Details represents a MediaItem.

Media Details should not care where it was opened from.

Esc returns to the originating screen.

---

# Media Details Layout

Use compact aligned formatting.

Example:

```text
Artist:      Linkin Park
Album:       Meteora
Duration:    3:08
Status:      Downloaded
Source:      Spotify
```

Avoid:

```text
Artist:
Linkin Park
```

style layouts.

---

# Media Details Actions

```text
Esc Back

A Archive
M Rematch
D Download
V Review
```

---

# Exports

The Exports screen manages persistent exports.

One-time exports are not stored.

List format:

```text
✓ Car USB               1543 MB
! Phone Music            872 MB
? Backup Drive             0 MB
```

Status symbols:

```text
✓ Up To Date
! Out Of Date
? Missing
```

Only symbols are shown.

Size appears on the right.

---

# Export Details

Displays:

- Path
- Status
- Size
- Collection Count
- Last Update

Actions:

```text
Esc Back

U Update
R Recover
D Delete
```

---

# Jobs

Jobs are displayed as a navigable list.

Each job should consume approximately 2-3 lines.

Example:

```text
Download Collection
[████████░░░░░░░░] 18 / 43
```

The Jobs screen should not be a static display.

Users can select jobs and perform actions.

---

# Job Actions

Running:

```text
Cancel
Pause
```

Paused:

```text
Resume
Cancel
```

Failed:

```text
Retry
Remove
```

Completed:

```text
Remove
```

Cancelled:

```text
Remove
```

Completed jobs remain visible until manually removed.

---

# Settings

Supports:

- Simple editable values
- Nested settings screens

Examples:

```text
Library Root

Database Path

Export Roots

Matching Penalties
```

---

# Export Roots

Dedicated screen.

Supports:

- Add
- Remove
- Edit

---

# Matching Penalties

Dedicated screen.

Supports:

- Add
- Remove
- Edit

---

# Jump

Shortcut:

```text
J
```

Jump performs incremental prefix matching.

Example:

```text
Jump: ro
```

moves to:

```text
Road Trip Playlist
```

Rules:

- List ordering never changes
- Prefix matching only
- User may continue navigating while Jump is active
- Backspace restores previous jump state
- Enter accepts current location
- Esc restores original location

If no match exists:

- Cursor remains at previous valid match
- UI indicates no match found

---

# Find

Shortcut:

```text
F
```

Find performs incremental fuzzy searching.

Rules:

- List is reordered by relevance
- Best match appears first
- No score threshold exists
- Poor matches are still shown
- User decides relevance

Enter:

- Keeps current location
- Closes Find

Esc:

- Restores original ordering
- Restores original location

---

# Filters

Shortcut:

```text
T
```

Filters are query-based.

Filters are not text searches.

Examples:

```text
Downloaded

Review

Archived

Not Archived
```

Collections:

- Persist while inside Collections

Collection Details:

- Persist while inside Collection Details

Media:

- Persist while inside Media

Leaving the screen resets filters.

---

# Review Workflow

There is no Review Queue screen.

Review operates on visible query results.

Examples:

```text
Media
    ↓ Filter
    ↓ V
Review Session
```

or:

```text
Collection Details
    ↓ Filter
    ↓ V
Review Session
```

Review automatically skips items that do not require review.

---

# Query System

Queries are the foundation of:

- Filters
- Review
- Exports
- Bulk Actions

Examples:

```python
MediaQuery(
    archived=False,
    status=REVIEW
)
```

```python
CollectionQuery(
    source_type=SPOTIFY
)
```

UI filtering should always be implemented through queries.

Never create separate UI-side filtering systems.

---

# Jobs Philosophy

Jobs are:

- Atomic
- In-memory
- Non-persistent

Jobs support:

- Cancel
- Pause
- Resume
- Retry
- Remove

Retry creates a new job.

Completed jobs remain visible until manually removed.

---

# Core Separation

Business logic belongs in Core.

The TUI should:

- Display data
- Submit actions
- Navigate screens

The TUI should not:

- Perform matching
- Perform filtering logic
- Manage job state
- Duplicate query logic