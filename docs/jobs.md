# MusicSync Jobs

## Purpose

Jobs execute long-running operations in the background.

Examples:

- Refresh Collection
- Download Collection
- Export Update
- Library Verification

---

# Design Goals

Jobs are:

- Atomic
- In-memory
- Non-persistent
- UI-agnostic

Jobs are not:

- Scheduled
- Persistent
- Distributed
- Workflow engines

---

# Lifecycle

```text
QUEUED
    ↓
RUNNING
    ↓
COMPLETED

RUNNING
    ↓
FAILED

RUNNING
    ↓
CANCELLED

RUNNING
    ↓
PAUSED
    ↓
RUNNING
```

---

# Cancellation

Cancellation is cooperative.

Worker functions should periodically check:

```python
job_context.is_cancelled
```

and exit cleanly.

Threads are never forcibly terminated.

---

# Pause / Resume

Pausing is cooperative.

Worker functions should periodically call:

```python
job_context.wait_if_paused()
```

The thread remains alive while paused.

---

# Retry

Retry creates a completely new Job.

A retry never mutates an existing Job.

Example:

```text
Failed Job
    ↓ Retry
New Job
```

The original Job remains visible.

---

# Removal

Completed jobs remain visible until explicitly removed.

Jobs are not automatically deleted.

Users may remove:

- Completed Jobs
- Failed Jobs
- Cancelled Jobs

Users may not remove:

- Running Jobs
- Paused Jobs
- Queued Jobs

---

# Progress

Jobs expose:

```text
Current Progress
Total Progress
Current Item
Progress Message
```

Examples:

```text
18 / 43
```

```text
Matching candidates
```

```text
Linkin Park - Numb
```

---

# Job Ownership

JobManager owns:

- Job lifecycle
- Status transitions
- Thread execution
- Action availability

The TUI owns:

- Display
- User interaction

The TUI should never contain lifecycle logic.

---

# Action Availability

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

These rules should originate from JobManager.

The TUI should not duplicate them.