const runListEl = document.getElementById('run-list');
const runListErrorEl = document.getElementById('run-list-error');
const btnRefreshEl = document.getElementById('btn-refresh');
const btnCopyLinkEl = document.getElementById('btn-copy-link');
const btnCopyRunIdEl = document.getElementById('btn-copy-run-id');
const btnCopyPathEl = document.getElementById('btn-copy-path');
const btnRenameRunEl = document.getElementById('btn-rename-run');
const btnDeleteRunEl = document.getElementById('btn-delete-run');
const runSummaryEl = document.getElementById('run-summary');
const runSummaryStatusEl = document.getElementById('run-summary-status');
const runSummaryKpisEl = document.getElementById('run-summary-kpis');
const runSummaryCalloutsEl = document.getElementById('run-summary-callouts');
const runSummaryFiltersEl = document.getElementById('run-summary-filters');
const timelineToolbarEl = document.getElementById('timeline-toolbar');
const eventCountEl = document.getElementById('event-count');
const timelineEmptyEl = document.getElementById('timeline-empty');
const timelineErrorEl = document.getElementById('timeline-error');
const timelineEventsEl = document.getElementById('timeline-events');

let currentEvents = [];
let currentFilter = 'all';
let lastRuns = [];
let currentRunId = null;
let currentRunMeta = null;
let fetchAbort = null;
let runListPollIntervalId = null;
let eventPollIntervalId = null;
const escapeDiv = document.createElement('div');

// Filter value in URL vs internal event_type. Default All; URL persists so refresh keeps it.
const FILTER_URL_MAP = { all: 'all', llm: 'LLM_CALL', tools: 'TOOL_CALL', errors: 'ERROR', state: 'STATE_UPDATE', loops: 'LOOP_WARNING' };
const FILTER_LABELS = { all: 'All', LLM_CALL: 'LLM', TOOL_CALL: 'Tools', ERROR: 'Errors', STATE_UPDATE: 'State', LOOP_WARNING: 'Loops' };

/** Poll intervals in ms. Read from URL ?poll_runs=5&poll_events=3 (seconds); defaults 3s and 2s; clamped 1–60s. */
function getPollIntervalSeconds(name, defaultSec) {
  const url = new URL(window.location.href);
  const v = url.searchParams.get(name);
  if (v == null || v === '') return defaultSec;
  const n = parseInt(v, 10);
  if (Number.isNaN(n)) return defaultSec;
  return Math.max(1, Math.min(60, n));
}
const POLL_RUNS_MS = getPollIntervalSeconds('poll_runs', 3) * 1000;
const POLL_EVENTS_MS = getPollIntervalSeconds('poll_events', 2) * 1000;

function getRunIdFromUrl() {
  const url = new URL(window.location.href);
  const q = url.searchParams.get('run') || url.searchParams.get('run_id');
  if (q) return q;
  const parts = url.pathname.split('/').filter(Boolean);
  if (parts.length === 0) return null;
  const last = parts[parts.length - 1];
  const looksLikeId = /^[0-9a-fA-F-]{8,}$/.test(last);
  return looksLikeId ? last : null;
}

function getFilterFromUrl() {
  const url = new URL(window.location.href);
  const q = url.searchParams.get('filter');
  if (!q) return 'all';
  const v = FILTER_URL_MAP[q.toLowerCase()];
  return v != null ? v : 'all';
}

function getRunUrl(runId) {
  if (!runId) return '';
  const url = new URL(window.location.href);
  url.pathname = '/';
  url.searchParams.set('run', runId);
  if (currentFilter !== 'all') url.searchParams.set('filter', getFilterUrlValue(currentFilter));
  return url.toString();
}

function getFilterUrlValue(filterValue) {
  if (filterValue === 'all') return 'all';
  const entry = Object.entries(FILTER_URL_MAP).find(([, v]) => v === filterValue);
  return entry ? entry[0] : 'all';
}

function setUrlRunId(runId, { replace = false } = {}) {
  if (!runId) return;
  const url = new URL(window.location.href);
  url.pathname = '/';
  url.searchParams.set('run', runId);
  url.searchParams.set('filter', getFilterUrlValue(currentFilter));
  const method = replace ? 'replaceState' : 'pushState';
  window.history[method]({ run_id: runId, filter: currentFilter }, '', url.toString());
}

function setUrlFilter(filterValue) {
  const url = new URL(window.location.href);
  url.searchParams.set('filter', getFilterUrlValue(filterValue));
  window.history.replaceState({ run_id: currentRunId, filter: filterValue }, '', url.toString());
}

(function canonicalizeOnBoot() {
  const url = new URL(window.location.href);
  const runIdFromQuery = url.searchParams.get('run') || url.searchParams.get('run_id');
  const runIdFromPath = getRunIdFromUrl();
  const runId = runIdFromQuery || runIdFromPath;
  if (runId) {
    currentFilter = getFilterFromUrl();
    setUrlRunId(runId, { replace: true });
  }
})();

function updateCopyButtonsState() {
  const hasRun = !!currentRunId;
  if (btnCopyLinkEl) btnCopyLinkEl.disabled = !hasRun;
  if (btnCopyRunIdEl) btnCopyRunIdEl.disabled = !hasRun;
  if (btnCopyPathEl) btnCopyPathEl.disabled = !hasRun;
  if (btnRenameRunEl) btnRenameRunEl.disabled = !hasRun;
  if (btnDeleteRunEl) btnDeleteRunEl.disabled = !hasRun;
}

async function copyRunLink() {
  if (!currentRunId) return;
  const url = getRunUrl(currentRunId);
  try {
    await navigator.clipboard.writeText(url);
    if (btnCopyLinkEl) btnCopyLinkEl.textContent = 'Copied!';
    setTimeout(() => { if (btnCopyLinkEl) btnCopyLinkEl.textContent = 'Copy link'; }, 1500);
  } catch (_) {}
}

async function copyRunId() {
  if (!currentRunId) return;
  try {
    await navigator.clipboard.writeText(currentRunId);
    if (btnCopyRunIdEl) btnCopyRunIdEl.textContent = 'Copied!';
    setTimeout(() => { if (btnCopyRunIdEl) btnCopyRunIdEl.textContent = 'Copy run ID'; }, 1500);
  } catch (_) {}
}

async function copyRunPath() {
  if (!currentRunId) return;
  try {
    const r = await fetch('/api/runs/' + encodeURIComponent(currentRunId) + '/paths');
    if (!r.ok) throw new Error(r.statusText || 'Failed to load paths');
    const data = await r.json();
    const paths = data.paths || {};
    // const metaJsonPath = paths.meta_json;
    // if (!metaJsonPath) throw new Error('meta.json path unavailable');
    // await navigator.clipboard.writeText(metaJsonPath);
    const runDirPath = paths.run_dir;
    if (!runDirPath) throw new Error('run directory path unavailable');
    await navigator.clipboard.writeText(runDirPath);
    if (btnCopyPathEl) btnCopyPathEl.textContent = 'Copied!';
    setTimeout(() => { if (btnCopyPathEl) btnCopyPathEl.textContent = 'Copy path'; }, 1500);
  } catch (_) {}
}

async function renameRun() {
  if (!currentRunId) return;
  const currentName = currentRunMeta?.run_name || '';
  const msg =
    'Enter a new name for this run. This will update its meta.json file on disk.';
  const nextName = window.prompt(msg, currentName);
  if (nextName == null) return;
  const trimmed = nextName.trim();
  if (!trimmed || trimmed === currentName) return;

  try {
    const r = await fetch('/api/runs/' + encodeURIComponent(currentRunId) + '/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_name: trimmed }),
    });
    if (!r.ok) {
      throw new Error(r.statusText || 'Failed to rename run');
    }
    const meta = await r.json();
    currentRunMeta = meta;
    renderRunSummary(currentRunMeta, currentEvents.length ? currentEvents : null);
    // Refresh sidebar metadata so the name updates there as well.
    pollRunList();
  } catch (e) {
    window.alert(e.message || 'Failed to rename run');
  }
}

async function deleteRun() {
  if (!currentRunId) return;
  const ok = window.confirm(
    'Delete this run permanently?\n\nThis will remove its directory and trace files from local storage. This action cannot be undone.',
  );
  if (!ok) return;

  const runIdToDelete = currentRunId;
  try {
    if (btnDeleteRunEl) btnDeleteRunEl.disabled = true;
    const r = await fetch('/api/runs/' + encodeURIComponent(runIdToDelete), {
      method: 'DELETE',
    });
    if (!r.ok && r.status !== 404) {
      throw new Error(r.statusText || 'Failed to delete run');
    }

    // Clear run from URL so reload doesn't try to re-select a deleted ID.
    const url = new URL(window.location.href);
    url.searchParams.delete('run');
    url.searchParams.delete('run_id');
    window.history.replaceState({ run_id: null, filter: currentFilter }, '', url.toString());

    // Reload run list; selection/fallback handled by loadRuns().
    await loadRuns();
  } catch (e) {
    window.alert(e.message || 'Failed to delete run');
  } finally {
    if (btnDeleteRunEl) btnDeleteRunEl.disabled = !currentRunId;
  }
}

function showRunListError(msg) {
  runListErrorEl.textContent = msg;
  runListErrorEl.style.display = 'block';
}
function hideRunListError() {
  runListErrorEl.style.display = 'none';
}
const runNotFoundBannerEl = document.getElementById('run-not-found-banner');
function showRunNotFoundBanner() {
  if (runNotFoundBannerEl) runNotFoundBannerEl.style.display = 'block';
}
function hideRunNotFoundBanner() {
  if (runNotFoundBannerEl) runNotFoundBannerEl.style.display = 'none';
}
function setRunListLoading(loading) {
  if (loading) {
    runListEl.innerHTML = '<div class="run-list-loading"><span class="spinner"></span><span>Loading…</span></div>';
    if (btnRefreshEl) btnRefreshEl.disabled = true;
  } else {
    if (btnRefreshEl) btnRefreshEl.disabled = false;
  }
}
function showTimelineError(msg) {
  timelineEmptyEl.style.display = 'none';
  timelineEventsEl.innerHTML = '';
  timelineErrorEl.textContent = msg;
  timelineErrorEl.style.display = 'block';
}
function hideTimelineError() {
  timelineErrorEl.style.display = 'none';
}

async function loadRuns() {
  hideRunListError();
  setRunListLoading(true);
  try {
    const r = await fetch('/api/runs');
    if (!r.ok) throw new Error(r.statusText || 'Failed to load runs');
    const data = await r.json();
    const runs = data.runs || [];
    lastRuns = runs;
    runListEl.innerHTML = '';
    if (runs.length === 0) {
      runListEl.innerHTML = '<div class="empty">No runs yet.</div>';
    } else {
      const frag = document.createDocumentFragment();
      runs.forEach((run) => {
        frag.appendChild(buildRunItemEl(run));
      });
      runListEl.appendChild(frag);
      const urlRunId = getRunIdFromUrl();
      let toSelect = runs[0].trace_id;
      if (urlRunId) {
        const exact = runs.find((r) => r.trace_id === urlRunId);
        const byPrefix = runs.find((r) => r.trace_id && r.trace_id.startsWith(urlRunId));
        if (exact) toSelect = exact.trace_id;
        else if (byPrefix) toSelect = byPrefix.trace_id;
        else {
          showRunNotFoundBanner();
          selectRun(runs[0].trace_id, { fromFallback: true });
          return;
        }
      }
      selectRun(toSelect, { initialLoad: true, forceRefresh: true });
    }
    updateCopyButtonsState();
  } catch (e) {
    showRunListError(e.message || 'Failed to load runs');
  } finally {
    setRunListLoading(false);
    updateCopyButtonsState();
    if (document.visibilityState === 'visible') startRunListPolling();
  }
}

function escapeHtml(s) {
  if (s == null) return '';
  escapeDiv.textContent = s;
  return escapeDiv.innerHTML;
}

/**
 * UI status for a run: completed ok with any loop warning counts as "warning"
 * (meta.json still stores status "ok"; list + summary show warning).
 */
function effectiveRunUiStatus(run) {
  const raw = (run.status || '').toLowerCase();
  if (raw === 'running') return 'running';
  if (raw === 'error') return 'error';
  const lw = run.counts && run.counts.loop_warnings != null ? Number(run.counts.loop_warnings) : 0;
  if (lw > 0 && (raw === 'ok' || raw === '')) return 'warning';
  return raw === 'ok' || raw === '' ? 'ok' : raw;
}

/** Sidebar meta line HTML: timestamp · status pill (colors match .event cards) · duration. */
function formatRunItemMetaHtml(run) {
  const parts = [];
  if (run.started_at) parts.push(escapeHtml(run.started_at));
  const uiStatus = effectiveRunUiStatus(run);
  const known = { ok: true, warning: true, error: true, running: true };
  const kind = known[uiStatus] ? uiStatus : 'ok';
  const label = known[uiStatus] ? uiStatus : escapeHtml(uiStatus);
  parts.push('<span class="run-item-status ' + kind + '">' + label + '</span>');
  if (run.duration_ms != null) parts.push(escapeHtml(String(run.duration_ms) + ' ms'));
  return parts.join(' · ');
}

/** Build one sidebar run item element (shared by loadRuns and mergeRunListIntoSidebar). */
function buildRunItemEl(run) {
  const div = document.createElement('div');
  div.className = 'run-item' + (run.status === 'running' ? ' running' : '');
  div.dataset.runId = run.trace_id;
  const name = run.run_name || run.trace_id?.slice(0, 8) || '—';
  const nameHtml = run.status === 'running' ? '<span class="live-dot" aria-hidden="true"></span><span class="run-name">' + escapeHtml(name) + '</span>' : '<span class="run-name">' + escapeHtml(name) + '</span>';
  div.innerHTML = nameHtml + '<br><span class="run-meta">' + formatRunItemMetaHtml(run) + '</span>';
  div.addEventListener('click', () => selectRun(run.trace_id));
  return div;
}

function selectRun(runId, options) {
  const opts = options || {};
  if (runId === currentRunId && !opts.fromPopState && !opts.forceRefresh) return;
  currentRunId = runId;
  clearEventPollInterval();
  if (fetchAbort) {
    fetchAbort.abort();
    fetchAbort = null;
  }
  fetchAbort = new AbortController();
  const signal = fetchAbort.signal;
  if (!opts.fromPopState) {
    setUrlRunId(runId, { replace: !!(opts.fromFallback || opts.initialLoad) });
  }
  runListEl.querySelectorAll('.run-item').forEach((el) => {
    el.classList.toggle('selected', el.dataset.runId === runId);
  });
  if (!opts.fromFallback) hideRunNotFoundBanner();
  updateCopyButtonsState();
  loadRunMeta(runId, signal);
  loadEvents(runId, signal);
}

// Run Summary panel: single compact overview above the timeline.
// State flow: meta.json (loadRunMeta) -> currentRunMeta; events (loadEvents) -> currentEvents.
// We render status strip + KPI chips from run only; callouts (first error, loop warning, running)
// and filter row use events when available. If events fail to load, summary still shows from meta.json;
// jump links are hidden when events is null.
function renderRunSummary(run, events) {
  if (!runSummaryEl || !run) {
    if (runSummaryEl) runSummaryEl.style.display = 'none';
    return;
  }
  const counts = run.counts || {};
  const llm = counts.llm_calls != null ? counts.llm_calls : 0;
  const tools = counts.tool_calls != null ? counts.tool_calls : 0;
  const errors = counts.errors != null ? counts.errors : 0;
  const loopWarnings = counts.loop_warnings != null ? counts.loop_warnings : 0;
  const status = effectiveRunUiStatus(run);
  const runName = run.run_name || (run.trace_id || currentRunId || '').slice(0, 8);
  const shortId = (run.trace_id || currentRunId || '').slice(0, 8);

  // Status strip: badge, run name, started_at, duration, short id + copy
  runSummaryStatusEl.textContent = '';
  const badge = document.createElement('span');
  const badgeKind = status === 'ok' ? 'ok' : status === 'error' ? 'error' : status === 'warning' ? 'warning' : 'running';
  badge.className = 'status-badge ' + badgeKind;
  badge.textContent = status === 'ok' ? 'OK' : status === 'error' ? 'ERROR' : status === 'warning' ? 'WARNING' : 'RUNNING';
  runSummaryStatusEl.appendChild(badge);
  const nameSpan = document.createElement('span');
  nameSpan.className = 'run-name';
  nameSpan.textContent = runName;
  runSummaryStatusEl.appendChild(nameSpan);
  const metaSpan = document.createElement('span');
  metaSpan.className = 'run-meta';
  metaSpan.textContent = [run.started_at || '—', run.duration_ms != null ? run.duration_ms + ' ms' : ''].filter(Boolean).join(' · ');
  runSummaryStatusEl.appendChild(metaSpan);
  const idWrap = document.createElement('span');
  idWrap.className = 'run-id-wrap';
  const idText = document.createElement('span');
  idText.textContent = shortId;
  idWrap.appendChild(idText);
  const copyIdBtn = document.createElement('button');
  copyIdBtn.type = 'button';
  copyIdBtn.className = 'btn-copy-id';
  copyIdBtn.textContent = 'Copy';
  copyIdBtn.setAttribute('aria-label', 'Copy run ID');
  copyIdBtn.addEventListener('click', () => copyRunId());
  idWrap.appendChild(copyIdBtn);
  runSummaryStatusEl.appendChild(idWrap);

  // KPI chips
  runSummaryKpisEl.textContent = '';
  ['llm_calls', 'tool_calls', 'errors', 'loop_warnings'].forEach((key) => {
    const label = key === 'llm_calls' ? 'LLM' : key === 'tool_calls' ? 'Tools' : key === 'errors' ? 'Errors' : 'Loop warnings';
    const val = counts[key] != null ? counts[key] : 0;
    const chip = document.createElement('span');
    chip.className = 'kpi-chip';
    const vSpan = document.createElement('span');
    vSpan.className = 'kpi-value';
    vSpan.textContent = String(val);
    chip.appendChild(document.createTextNode(label + ': '));
    chip.appendChild(vSpan);
    runSummaryKpisEl.appendChild(chip);
  });

  // Callouts: only when relevant; jump links only when events available
  runSummaryCalloutsEl.textContent = '';
  if (errors > 0 && Array.isArray(events) && events.length > 0) {
    const firstErrorIdx = events.findIndex((ev) => (ev.event_type || '') === 'ERROR');
    if (firstErrorIdx !== -1) {
      const eventNum = firstErrorIdx + 1;
      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'callout error';
      link.textContent = 'First error at event #' + eventNum;
      link.setAttribute('aria-label', 'Jump to first error, event ' + eventNum);
      link.addEventListener('click', () => jumpToEvent(firstErrorIdx, 'ERROR'));
      runSummaryCalloutsEl.appendChild(link);
    }
  }
  if (loopWarnings > 0 && Array.isArray(events) && events.length > 0) {
    const firstLoopIdx = events.findIndex((ev) => (ev.event_type || '') === 'LOOP_WARNING');
    if (firstLoopIdx !== -1) {
      const eventNum = firstLoopIdx + 1;
      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'callout';
      link.textContent = 'Loop warning detected';
      link.setAttribute('aria-label', 'Jump to first loop warning, event ' + eventNum);
      link.addEventListener('click', () => jumpToEvent(firstLoopIdx, 'LOOP_WARNING'));
      runSummaryCalloutsEl.appendChild(link);
    }
  }
  if (status === 'running') {
    const refreshBtn = document.createElement('button');
    refreshBtn.type = 'button';
    refreshBtn.className = 'callout callout-refresh';
    refreshBtn.textContent = 'Run still in progress — Refresh';
    refreshBtn.setAttribute('aria-label', 'Refresh run');
    refreshBtn.addEventListener('click', () => {
      if (currentRunId) {
        const ac = new AbortController();
        loadRunMeta(currentRunId, ac.signal);
        loadEvents(currentRunId, ac.signal);
      }
    });
    runSummaryCalloutsEl.appendChild(refreshBtn);
  }

  // Quick filters row: All, LLM, Tools, Errors, State, Loops; state in URL
  runSummaryFiltersEl.textContent = '';
  const filterValues = ['all', 'LLM_CALL', 'TOOL_CALL', 'ERROR', 'STATE_UPDATE', 'LOOP_WARNING'];
  filterValues.forEach((fv) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'filter-chip' + (currentFilter === fv ? ' active' : '');
    btn.textContent = FILTER_LABELS[fv] || fv;
    btn.addEventListener('click', () => {
      currentFilter = fv;
      setUrlFilter(fv);
      setSummaryFilterActive();
      renderEvents();
    });
    runSummaryFiltersEl.appendChild(btn);
  });

  runSummaryEl.style.display = 'block';
}

function setSummaryFilterActive() {
  if (!runSummaryFiltersEl) return;
  runSummaryFiltersEl.querySelectorAll('.filter-chip').forEach((el, i) => {
    const fv = ['all', 'LLM_CALL', 'TOOL_CALL', 'ERROR', 'STATE_UPDATE', 'LOOP_WARNING'][i];
    el.classList.toggle('active', currentFilter === fv);
  });
}

// Scroll to event at index in currentEvents, expand it, highlight briefly. Filter is set to all so it's visible.
function jumpToEvent(indexInCurrentEvents, eventType) {
  currentFilter = 'all';
  setUrlFilter('all');
  setSummaryFilterActive();
  renderEvents();
  requestAnimationFrame(() => {
    const el = timelineEventsEl.querySelector('[data-event-index="' + indexInCurrentEvents + '"]');
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    const summary = el.querySelector('.event-summary');
    const details = el.querySelector('.event-details');
    const toggle = el.querySelector('.toggle');
    if (details && details.style.display !== 'block') {
      details.style.display = 'block';
      if (toggle) toggle.textContent = '▼';
    }
    el.classList.add('highlight');
    setTimeout(() => el.classList.remove('highlight'), 2000);
  });
}

async function loadRunMeta(runId, signal) {
  try {
    const r = await fetch('/api/runs/' + encodeURIComponent(runId), { signal });
    if (r.status === 404) {
      runSummaryEl.style.display = 'none';
      currentRunMeta = null;
      return;
    }
    if (!r.ok) throw new Error(r.statusText || 'Failed to load run');
    const run = await r.json();
    if (signal?.aborted) return;
    currentRunMeta = run;
    renderRunSummary(run, currentEvents.length ? currentEvents : null);
    if (run.status === 'running' && document.visibilityState === 'visible') startEventPolling();
  } catch (e) {
    if (e.name === 'AbortError') return;
    runSummaryEl.style.display = 'none';
    currentRunMeta = null;
  }
}

function durationLabel(ms) {
  return ms != null ? ms + ' ms' : '—';
}

// Build one timeline event element. indexInCurrentEvents is used for jump-to-event (data-event-index).
function buildEventEl(ev, indexInCurrentEvents) {
  const isLoop = ev.event_type === 'LOOP_WARNING';
  const isError = ev.event_type === 'ERROR';
  let className = 'event';
  if (isLoop) className += ' loop-warning';
  if (isError) className += ' error';
  const div = document.createElement('div');
  div.className = className;
  div.dataset.eventType = ev.event_type || '';
  if (indexInCurrentEvents != null) div.dataset.eventIndex = String(indexInCurrentEvents);
  const summary = document.createElement('div');
  summary.className = 'event-summary';
  summary.innerHTML = '<span class="toggle">▶</span><span class="type">' + escapeHtml(ev.event_type || '') + '</span><span class="name">' + escapeHtml(ev.name || '') + '</span><span class="duration">' + escapeHtml(durationLabel(ev.duration_ms)) + '</span><span class="ts">' + escapeHtml(ev.ts || '') + '</span>';
  const details = document.createElement('div');
  details.className = 'event-details';
  details.style.display = 'none';
  const payloadStr = JSON.stringify(ev.payload != null ? ev.payload : {}, null, 2);
  const metaStr = JSON.stringify(ev.meta != null ? ev.meta : {}, null, 2);
  details.innerHTML =
    '<div class="row"><span class="label">event_type:</span><pre>' + escapeHtml(ev.event_type || '') + '</pre></div>' +
    '<div class="row"><span class="label">ts:</span><pre>' + escapeHtml(ev.ts || '') + '</pre></div>' +
    '<div class="row"><span class="label">name:</span><pre>' + escapeHtml(ev.name || '') + '</pre></div>' +
    '<div class="row"><span class="label">duration_ms:</span><pre>' + escapeHtml(ev.duration_ms != null ? String(ev.duration_ms) : 'null') + '</pre></div>' +
    '<div class="row"><span class="label">payload:</span><pre>' + escapeHtml(payloadStr) + '</pre></div>' +
    '<div class="row"><span class="label">meta:</span><pre>' + escapeHtml(metaStr) + '</pre></div>';
  summary.addEventListener('click', () => {
    const open = details.style.display !== 'none';
    details.style.display = open ? 'none' : 'block';
    summary.querySelector('.toggle').textContent = open ? '▶' : '▼';
  });
  div.appendChild(summary);
  div.appendChild(details);
  return div;
}

function renderToolbar(events) {
  const n = events.length;
  eventCountEl.textContent = n + ' event' + (n === 1 ? '' : 's');
  timelineToolbarEl.style.display = 'flex';
}

function renderEvents() {
  const frag = document.createDocumentFragment();
  currentEvents.forEach((ev, i) => {
    if (currentFilter === 'all' || (ev.event_type || '') === currentFilter) {
      frag.appendChild(buildEventEl(ev, i));
    }
  });
  timelineEventsEl.innerHTML = '';
  timelineEventsEl.appendChild(frag);
}

/** Indices in `currentEvents` whose detail rows are expanded (used to survive live poll re-renders). */
function collectExpandedEventIndices() {
  const open = new Set();
  timelineEventsEl.querySelectorAll('.event[data-event-index]').forEach((el) => {
    const raw = el.dataset.eventIndex;
    const details = el.querySelector('.event-details');
    if (raw == null || !details || details.style.display !== 'block') return;
    const idx = parseInt(raw, 10);
    if (!Number.isNaN(idx)) open.add(idx);
  });
  return open;
}

function restoreExpandedEventIndices(indices) {
  if (!indices || indices.size === 0) return;
  timelineEventsEl.querySelectorAll('.event[data-event-index]').forEach((el) => {
    const raw = el.dataset.eventIndex;
    if (raw == null) return;
    const idx = parseInt(raw, 10);
    if (Number.isNaN(idx) || !indices.has(idx)) return;
    const details = el.querySelector('.event-details');
    const toggle = el.querySelector('.toggle');
    if (details) details.style.display = 'block';
    if (toggle) toggle.textContent = '▼';
  });
}

async function loadEvents(runId, signal) {
  hideTimelineError();
  timelineEmptyEl.style.display = 'none';
  timelineEventsEl.innerHTML = '<div class="empty">Loading…</div>';
  timelineToolbarEl.style.display = 'none';
  try {
    const r = await fetch('/api/runs/' + encodeURIComponent(runId) + '/spans', { signal });
    if (r.status === 404) {
      showRunNotFoundBanner();
      if (fetchAbort) {
        fetchAbort.abort();
        fetchAbort = null;
      }
      if (lastRuns.length > 0) {
        selectRun(lastRuns[0].trace_id, { fromFallback: true });
        return;
      }
      const listRes = await fetch('/api/runs', { signal });
      if (listRes.ok) {
        const listData = await listRes.json();
        const runs = listData.runs || [];
        lastRuns = runs;
        if (runs.length > 0) {
          selectRun(runs[0].trace_id, { fromFallback: true });
          return;
        }
      }
      showTimelineError('Run not found.');
      return;
    }
    if (!r.ok) throw new Error(r.statusText || 'Failed to load events');
    const data = await r.json();
    if (signal?.aborted) return;
    const events = data.events || [];
    currentEvents = events;
    currentFilter = getFilterFromUrl();
    if (currentRunMeta) renderRunSummary(currentRunMeta, currentEvents);
    timelineEventsEl.innerHTML = '';
    if (events.length === 0) {
      timelineEmptyEl.textContent = 'No events for this run.';
      timelineEmptyEl.style.display = 'block';
      return;
    }
    renderToolbar(events);
    renderEvents();
    if (currentRunMeta?.status === 'running' && document.visibilityState === 'visible') startEventPolling();
  } catch (e) {
    if (e.name === 'AbortError') return;
    currentEvents = [];
    if (currentRunMeta) renderRunSummary(currentRunMeta, null);
    showTimelineError(e.message || 'Failed to load events');
  }
}

// --- Live refresh: run list poll (3s), event poll (2s when run is running), visibility gating ---

function clearRunListPollInterval() {
  if (runListPollIntervalId != null) {
    clearInterval(runListPollIntervalId);
    runListPollIntervalId = null;
  }
}

function clearEventPollInterval() {
  if (eventPollIntervalId != null) {
    clearInterval(eventPollIntervalId);
    eventPollIntervalId = null;
  }
}

function startRunListPolling() {
  if (document.visibilityState !== 'visible') return;
  clearRunListPollInterval();
  runListPollIntervalId = setInterval(pollRunList, POLL_RUNS_MS);
}

function startEventPolling() {
  if (!currentRunId || currentRunMeta?.status !== 'running' || document.visibilityState !== 'visible') return;
  clearEventPollInterval();
  eventPollIntervalId = setInterval(pollEventsForCurrentRun, POLL_EVENTS_MS);
}

/** Merge API runs into sidebar: add new run items, update existing meta and running state. Does not replace list or change selection. */
function mergeRunListIntoSidebar(runs) {
  if (!runs || runs.length === 0) return;
  for (let i = 0; i < runs.length; i++) {
    const run = runs[i];
    const existing = runListEl.querySelector('.run-item[data-run-id="' + run.trace_id + '"]');
    const name = run.run_name || run.trace_id?.slice(0, 8) || '—';
    if (existing) {
      const nameEl = existing.querySelector('.run-name');
      const metaEl = existing.querySelector('.run-meta');
      if (nameEl) nameEl.textContent = name;
      if (metaEl) metaEl.innerHTML = formatRunItemMetaHtml(run);
      existing.classList.toggle('running', run.status === 'running');
      let dot = existing.querySelector('.live-dot');
      if (run.status === 'running') {
        if (!dot) {
          dot = document.createElement('span');
          dot.className = 'live-dot';
          dot.setAttribute('aria-hidden', 'true');
          existing.insertBefore(dot, existing.firstChild);
        }
      } else {
        dot?.remove();
      }
    } else {
      const newEl = buildRunItemEl(run);
      const prevRun = runs[i - 1];
      const prevEl = prevRun ? runListEl.querySelector('.run-item[data-run-id="' + prevRun.trace_id + '"]') : null;
      runListEl.insertBefore(newEl, prevEl ? prevEl.nextSibling : runListEl.firstChild);
    }
  }
}

async function pollRunList() {
  if (runListEl.querySelector('.run-list-loading')) return;
  try {
    const r = await fetch('/api/runs');
    if (!r.ok) return;
    const data = await r.json();
    const runs = data.runs || [];
    mergeRunListIntoSidebar(runs);
    const runIds = new Set(runs.map((x) => x.trace_id));
    const items = Array.from(runListEl.querySelectorAll('.run-item'));
    let currentRunWasRemoved = false;
    for (const el of items) {
      if (!runIds.has(el.dataset.runId)) {
        if (el.dataset.runId === currentRunId) currentRunWasRemoved = true;
        el.remove();
      }
    }
    if (runs.length === 0) {
      runListEl.innerHTML = '<div class="empty">No runs yet.</div>';
    }
    lastRuns = runs;
    if (currentRunWasRemoved) {
      clearEventPollInterval();
      currentRunId = null;
      currentRunMeta = null;
      if (runSummaryEl) runSummaryEl.style.display = 'none';
      timelineEmptyEl.textContent = 'Select a run to view events.';
      timelineEmptyEl.style.display = 'block';
      timelineEventsEl.innerHTML = '';
      timelineToolbarEl.style.display = 'none';
      timelineErrorEl.style.display = 'none';
      hideRunNotFoundBanner();
      updateCopyButtonsState();
      if (runs.length > 0) selectRun(runs[0].trace_id, { fromFallback: true });
    } else {
      const cur = runs.find((x) => x.trace_id === currentRunId);
      if (cur) currentRunMeta = cur;
      if (currentRunMeta && currentRunMeta.status !== 'running') clearEventPollInterval();
      else if (currentRunMeta && currentRunMeta.status === 'running' && document.visibilityState === 'visible') startEventPolling();
    }
  } catch (_) {}
}

async function pollEventsForCurrentRun() {
  if (!currentRunId || currentRunMeta?.status !== 'running') return;
  const ac = new AbortController();
  const signal = ac.signal;
  try {
    const [metaRes, eventsRes] = await Promise.all([
      fetch('/api/runs/' + encodeURIComponent(currentRunId), { signal }),
      fetch('/api/runs/' + encodeURIComponent(currentRunId) + '/spans', { signal }),
    ]);
    if (signal?.aborted) return;
    if (!metaRes.ok || !eventsRes.ok) return;
    const run = await metaRes.json();
    const eventsData = await eventsRes.json();
    if (signal?.aborted) return;
    if (currentRunId !== (run.trace_id || currentRunId)) return;
    currentRunMeta = run;
    currentEvents = eventsData.events || [];
    renderRunSummary(currentRunMeta, currentEvents);
    if (currentEvents.length === 0) {
      timelineEmptyEl.textContent = 'No events for this run.';
      timelineEmptyEl.style.display = 'block';
      timelineEventsEl.innerHTML = '';
      timelineToolbarEl.style.display = 'none';
    } else {
      timelineEmptyEl.style.display = 'none';
      const expanded = collectExpandedEventIndices();
      renderToolbar(currentEvents);
      renderEvents();
      restoreExpandedEventIndices(expanded);
    }
    if (currentRunMeta.status !== 'running') clearEventPollInterval();
  } catch (e) {
    if (e.name === 'AbortError') return;
  }
}

window.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    clearRunListPollInterval();
    clearEventPollInterval();
  } else {
    startRunListPolling();
    pollRunList();
    if (currentRunMeta?.status === 'running') startEventPolling();
  }
});

window.addEventListener('popstate', () => {
  currentFilter = getFilterFromUrl();
  const runId = getRunIdFromUrl();
  const inList = runId && Array.from(runListEl.querySelectorAll('.run-item')).some((el) => el.dataset.runId === runId);
  if (inList) selectRun(runId, { fromPopState: true });
  else {
    setSummaryFilterActive();
    renderEvents();
  }
});

if (btnRefreshEl) btnRefreshEl.addEventListener('click', () => loadRuns());
if (btnCopyLinkEl) btnCopyLinkEl.addEventListener('click', copyRunLink);
if (btnCopyRunIdEl) btnCopyRunIdEl.addEventListener('click', copyRunId);
if (btnCopyPathEl) btnCopyPathEl.addEventListener('click', copyRunPath);
if (btnRenameRunEl) btnRenameRunEl.addEventListener('click', renameRun);
if (btnDeleteRunEl) btnDeleteRunEl.addEventListener('click', deleteRun);

updateCopyButtonsState();
loadRuns();
