# Viewer (timeline UI)

The Maida viewer is a local web UI for inspecting runs and their event timelines. It is served by `maida view` and uses only static HTML, CSS, and JavaScript—no build step.

---

## Usage

### Starting the viewer

```bash
maida view
```

This starts a server at **http://127.0.0.1:8712** (configurable with `--host` and `--port`) and opens the browser. The server runs until you press **Ctrl+C**. See the [CLI](cli.md) for options (`--no-browser`, `--json`, etc.).

### What you see

- **Sidebar (run list):** Recent runs, newest first. Each row shows run name, started time, status, and duration. A **pulsing dot** (or “live” indicator) marks runs that are still **running**.
- **Main area:** For the selected run:
  - **Run summary:** Status badge (OK / ERROR / RUNNING), KPIs (LLM calls, tools, errors, loop warnings), quick filters (All, LLM, Tools, Errors, State, Loops), and optional callouts (e.g. jump to first error).
  - **Timeline:** Events in order; each event can be expanded to see payload and meta as JSON.

### URL parameters

You can control the UI via the URL (and the UI keeps these in sync when you change run or filter):

| Parameter   | Description |
|------------|-------------|
| `run` or `run_id` | Which run to show (full 32-hex-character OTel trace ID or short prefix). |
| `filter`         | Event filter: `all`, `llm`, `tools`, `errors`, `state`, `loops`. |
| `poll_runs`      | Run-list poll interval in **seconds** (default `3`, min 1, max 60). |
| `poll_events`     | Event-list poll interval in **seconds** when the run is “running” (default `2`, min 1, max 60). |

**Examples:**

- `http://127.0.0.1:8712/?run=abc12345` — open a specific run (prefix).
- `http://127.0.0.1:8712/?poll_runs=5&poll_events=3` — poll run list every 5s, events every 3s.

### Renaming and deleting runs

The viewer supports renaming and deleting runs directly from the UI:

- **Rename:** Click the rename button next to the run summary. Enter a new name; this updates `run_name` in the run's `meta.json` file. The sidebar reflects the change on the next poll.
- **Delete:** Click the delete button. After confirmation, the run directory and all its contents are permanently removed from disk. The sidebar switches to another run or shows "No runs yet."

These operations use `POST /api/runs/{trace_id}/rename` and `DELETE /api/runs/{trace_id}` respectively. See [Architecture](architecture.md) for the full API surface.

### Live refresh

- The **run list** is polled every few seconds (see `poll_runs`). New runs appear in the sidebar without a full page reload. Runs that no longer exist on disk (e.g. you deleted the run directory) are removed from the sidebar on the next poll; if the run you were viewing is removed, the UI switches to another run or shows “No runs yet.”
- When the **current run** has status **running**, the **event list** is polled every few seconds (see `poll_events`). The timeline and summary update in place. Polling for that run stops when the run finishes (status `ok` or `error`).
- Polling **pauses** when the browser tab is not visible (Page Visibility API). It resumes when you switch back to the tab.

---

## Development

### Where the code lives

All viewer assets are under **`maida/ui_static/`**:

| File         | Role |
|--------------|------|
| `index.html` | Single-page shell; loads `styles.css` and `app.js`. |
| `app.js`     | Run list, timeline, polling, URL state, filters. |
| `styles.css` | Layout, theme, run item and event styles, live-dot animation. |
| `favicon.svg`| Tab icon. |

The server (see [Architecture](architecture.md)) serves these at `/`, `/styles.css`, `/app.js`, and `/favicon.svg` with `Cache-Control: no-cache` so edits are visible after refresh.

### Key behavior (app.js)

- **Initial load:** `loadRuns()` fetches `GET /api/runs`, renders the sidebar, selects a run from URL or latest, then `loadRunMeta` + `loadEvents` for that run.
- **Run list polling:** `pollRunList()` runs on an interval (when tab visible). It fetches `/api/runs`, merges new/updated runs into the sidebar (`mergeRunListIntoSidebar`), removes runs that are no longer in the API response, and syncs `currentRunMeta` from the list. Intervals are set from URL params `poll_runs` and `poll_events` (seconds, clamped 1–60).
- **Event polling:** When `currentRunMeta.status === 'running'` and the tab is visible, `pollEventsForCurrentRun()` runs on an interval: it fetches run meta and events, updates the timeline and summary, and stops the interval when the run is no longer running.
- **Visibility:** A `visibilitychange` listener clears both intervals when the tab is hidden and restarts them (and triggers an immediate run-list poll) when the tab becomes visible.

### Making changes

1. Start the viewer: `maida view` (optionally with `--no-browser` and open the URL manually).
2. Edit files under `maida/ui_static/`.
3. Reload the page to see changes (no build step).

For API contracts and storage layout, see [Architecture](architecture.md) and [Trace format](reference/trace-format.md).
