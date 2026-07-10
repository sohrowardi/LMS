"use strict";

/* ---------------------------------------------------------------------
   Extension tables — mirrors app/db.py exactly. Every extension listed
   here maps to an explicit icon; nothing recognized ever falls back to
   the generic file icon.
   --------------------------------------------------------------------- */
const VIDEO_EXTS = ["mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "rmvb", "3gp", "ts"];
const SUB_EXTS = ["srt", "sub", "ass", "idx"];

const ICONS = { folder: "📁", other: "📄" };
VIDEO_EXTS.forEach((e) => (ICONS[e] = "🎬"));
SUB_EXTS.forEach((e) => (ICONS[e] = "📝"));

function iconFor(item) {
  if (item.is_dir) return ICONS.folder;
  const ext = (item.ext || "").toLowerCase();
  if (ICONS[ext]) return ICONS[ext]; // explicit lookup — every video/sub ext is guaranteed present
  return ICONS.other;
}

function categoryFor(item) {
  if (item.is_dir) return { kind: "folder" };
  const ext = (item.ext || "").toLowerCase();
  if (VIDEO_EXTS.includes(ext)) return { kind: "video", ext };
  if (SUB_EXTS.includes(ext)) return { kind: "subtitle", ext };
  return { kind: "other" };
}

/* ---------------------------------------------------------------------
   Filter state + hierarchical checkbox sync
   --------------------------------------------------------------------- */
const filterState = {
  folders: false,
  other: false,
  video: Object.fromEntries(VIDEO_EXTS.map((e) => [e, false])),
  subtitle: Object.fromEntries(SUB_EXTS.map((e) => [e, false])),
};

const videoChildrenEl = document.getElementById("videoChildren");
const subChildrenEl = document.getElementById("subChildren");

function buildChildRow(group, ext) {
  const label = document.createElement("label");
  label.className = "check-row child";
  label.innerHTML = `<input type="checkbox" data-group="${group}" data-ext="${ext}"> .${ext}`;
  return label;
}
VIDEO_EXTS.forEach((e) => videoChildrenEl.appendChild(buildChildRow("video", e)));
SUB_EXTS.forEach((e) => subChildrenEl.appendChild(buildChildRow("subtitle", e)));

const parentCheckboxes = {
  video: document.querySelector('input[data-parent="video"]'),
  subtitle: document.querySelector('input[data-parent="subtitle"]'),
};
const standaloneCheckboxes = {
  folders: document.querySelector('input[data-key="folders"]'),
  other: document.querySelector('input[data-key="other"]'),
};

function childCheckboxes(group) {
  return Array.from(document.querySelectorAll(`input[data-group="${group}"]`));
}

function syncParentFromChildren(group) {
  const boxes = childCheckboxes(group);
  const checkedCount = boxes.filter((b) => b.checked).length;
  const parent = parentCheckboxes[group];
  if (checkedCount === 0) {
    parent.checked = false;
    parent.indeterminate = false;
  } else if (checkedCount === boxes.length) {
    parent.checked = true;
    parent.indeterminate = false;
  } else {
    parent.checked = false;
    parent.indeterminate = true;
  }
}

function setGroup(group, value) {
  childCheckboxes(group).forEach((b) => {
    b.checked = value;
    filterState[group][b.dataset.ext] = value;
  });
  parentCheckboxes[group].indeterminate = false;
  parentCheckboxes[group].checked = value;
}

// Parent toggles → cascade to every child.
Object.entries(parentCheckboxes).forEach(([group, el]) => {
  el.addEventListener("change", () => {
    setGroup(group, el.checked);
    applyFiltersAndRender();
  });
});

// Child toggles → update state + recompute parent's checked/indeterminate state.
[videoChildrenEl, subChildrenEl].forEach((container) => {
  container.addEventListener("change", (ev) => {
    const box = ev.target;
    if (box.matches('input[type="checkbox"][data-group]')) {
      const group = box.dataset.group;
      filterState[group][box.dataset.ext] = box.checked;
      syncParentFromChildren(group);
      applyFiltersAndRender();
    }
  });
});

// Standalone toggles (Folders / Other).
standaloneCheckboxes.folders.addEventListener("change", (e) => {
  filterState.folders = e.target.checked;
  applyFiltersAndRender();
});
standaloneCheckboxes.other.addEventListener("change", (e) => {
  filterState.other = e.target.checked;
  applyFiltersAndRender();
});

function anyFilterActive() {
  if (filterState.folders || filterState.other) return true;
  if (Object.values(filterState.video).some(Boolean)) return true;
  if (Object.values(filterState.subtitle).some(Boolean)) return true;
  return false;
}

function passesFilter(item) {
  if (!anyFilterActive()) return true; // default: show everything
  const cat = categoryFor(item);
  if (cat.kind === "folder") return filterState.folders;
  if (cat.kind === "video") return !!filterState.video[cat.ext];
  if (cat.kind === "subtitle") return !!filterState.subtitle[cat.ext];
  return filterState.other;
}

/* ---------------------------------------------------------------------
   Search — debounced, hits the backend, filtering is then 100% local
   --------------------------------------------------------------------- */
const searchInput = document.getElementById("searchInput");
const searchShell = document.getElementById("searchShell");
const resultsEl = document.getElementById("results");

let rawResults = [];
let searchTimer = null;
let lastQuery = "";

searchInput.addEventListener("input", () => {
  searchShell.classList.toggle("empty", searchInput.value.length === 0);
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 300);
});

async function runSearch() {
  const q = searchInput.value.trim();
  lastQuery = q;
  if (!q) {
    rawResults = [];
    resultsEl.innerHTML = `<div class="empty-state">Type above to search your indexed media servers.</div>`;
    return;
  }
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const data = await resp.json();
    if (lastQuery !== q) return; // a newer keystroke already superseded this request
    rawResults = data.results || [];
    applyFiltersAndRender();
  } catch (err) {
    resultsEl.innerHTML = `<div class="empty-state">Search failed — is the LMS server still running?</div>`;
  }
}

function applyFiltersAndRender() {
  if (!lastQuery) return;
  const filtered = rawResults.filter(passesFilter);
  renderResults(filtered);
}

function renderResults(items) {
  if (items.length === 0) {
    resultsEl.innerHTML = `<div class="empty-state">No matches${anyFilterActive() ? " for the current filters" : ""}.</div>`;
    return;
  }

  // Backend already returns items sorted by server order, then
  // folders → video → other, then name — so grouping consecutive
  // same-server items preserves that ordering exactly.
  const groups = [];
  for (const item of items) {
    const last = groups[groups.length - 1];
    if (last && last.server === item.server_name) {
      last.items.push(item);
    } else {
      groups.push({ server: item.server_name, items: [item] });
    }
  }

  resultsEl.innerHTML = "";
  for (const group of groups) {
    const wrap = document.createElement("div");
    wrap.className = "server-group";

    const head = document.createElement("div");
    head.className = "server-head";
    head.innerHTML = `<span class="prompt">~/${escapeHtml(group.server)}</span><span class="count">${group.items.length} result${group.items.length === 1 ? "" : "s"}</span>`;
    wrap.appendChild(head);

    for (const item of group.items) {
      wrap.appendChild(renderRow(item));
    }
    resultsEl.appendChild(wrap);
  }
}

function renderRow(item) {
  const row = document.createElement("div");
  row.className = "row";
  row.title = item.url;

  const icon = document.createElement("div");
  icon.className = "row-icon";
  icon.textContent = iconFor(item);
  row.appendChild(icon);

  const name = document.createElement("div");
  const cat = categoryFor(item);
  name.className = "row-name " + (cat.kind === "folder" ? "dir" : cat.kind === "video" ? "video" : cat.kind === "subtitle" ? "sub" : "");
  name.textContent = item.name;
  row.appendChild(name);

  const actionSlot = document.createElement("div");
  if (cat.kind === "video") {
    const btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.textContent = "📋 Copy Link";
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation(); // never trigger the row's own open-link behavior
      navigator.clipboard.writeText(item.url).then(() => {
        btn.textContent = "Copied!";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = "📋 Copy Link";
          btn.classList.remove("copied");
        }, 1500);
      });
    });
    actionSlot.appendChild(btn);
  }
  row.appendChild(actionSlot);

  row.addEventListener("click", () => window.open(item.url, "_blank", "noopener"));
  return row;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------------------------------------------------------------
   Stats
   --------------------------------------------------------------------- */
const statsLine = document.getElementById("statsLine");

async function refreshStats() {
  try {
    const resp = await fetch("/api/stats");
    const s = await resp.json();
    statsLine.innerHTML =
      `<span><b>${s.total || 0}</b> indexed items</span>` +
      `<span><b>${s.files || 0}</b> files</span>` +
      `<span><b>${s.folders || 0}</b> folders</span>` +
      `<span><b>${s.servers || 0}</b> servers</span>`;
  } catch {
    statsLine.textContent = "Could not load index stats.";
  }
}

/* ---------------------------------------------------------------------
   Re-index + live progress
   --------------------------------------------------------------------- */
const reindexBtn = document.getElementById("reindexBtn");
const progressPanel = document.getElementById("progressPanel");
const progressRows = document.getElementById("progressRows");
const progressSummary = document.getElementById("progressSummary");

let pollTimer = null;

reindexBtn.addEventListener("click", async () => {
  reindexBtn.disabled = true;
  const resp = await fetch("/api/reindex", { method: "POST" });
  if (resp.status === 409) {
    reindexBtn.disabled = false;
    return;
  }
  progressPanel.classList.add("show");
  pollStatus();
  pollTimer = setInterval(pollStatus, 1500);
});

async function pollStatus() {
  try {
    const resp = await fetch("/api/status");
    const data = await resp.json();
    renderProgress(data);
    if (!data.running) {
      clearInterval(pollTimer);
      pollTimer = null;
      reindexBtn.disabled = false;
      refreshStats();
      if (lastQuery) runSearch(); // refresh any results the user is currently looking at
      setTimeout(() => progressPanel.classList.remove("show"), 2500);
    }
  } catch {
    /* transient network hiccup while polling — try again next tick */
  }
}

function renderProgress(data) {
  const servers = data.servers || [];
  const done = servers.filter((s) => s.status === "done").length;
  progressSummary.textContent = `${done}/${servers.length} servers done`;

  progressRows.innerHTML = "";
  for (const s of servers) {
    const row = document.createElement("div");
    row.className = "progress-row";
    row.innerHTML = `
      <span class="dot ${s.status}"></span>
      <span class="progress-name">${escapeHtml(s.server_name)}${s.status === "error" ? ` — <span class="progress-error">${escapeHtml(s.error_message || "error")}</span>` : ""}</span>
      <span class="progress-count">visited <b>${s.pages_visited}</b> pages, found <b>${s.files_found}</b> files</span>
    `;
    progressRows.appendChild(row);
  }
}

/* ---------------------------------------------------------------------
   Quit
   --------------------------------------------------------------------- */
document.getElementById("quitBtn").addEventListener("click", async () => {
  if (!confirm("Shut down the LMS server? You'll need to relaunch it to search again.")) return;
  await fetch("/api/quit", { method: "POST" });
  document.body.innerHTML =
    '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:monospace;color:#8593a0;">LMS server stopped. You can close this tab.</div>';
});

/* ---------------------------------------------------------------------
   Init
   --------------------------------------------------------------------- */
refreshStats();
pollStatus(); // in case a crawl was already running when the page loaded
