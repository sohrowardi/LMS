"use strict";

/* ---------------------------------------------------------------------
   Icons — every video/subtitle extension resolves through classify.py's
   kind field already; "other" files get a further extension-specific
   icon so common types (pdf/zip/audio/image/etc.) aren't lumped into
   one generic icon.
   --------------------------------------------------------------------- */
const AUDIO_EXT = new Set([".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma", ".opus"]);
const IMAGE_EXT = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".tiff"]);
const ARCHIVE_EXT = new Set([".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"]);
const DOC_EXT = new Set([".doc", ".docx", ".txt", ".md", ".rtf", ".odt"]);
const EXE_EXT = new Set([".exe", ".sh", ".bat", ".msi", ".appimage"]);

function extOf(name) {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i).toLowerCase();
}

function iconFor(item) {
  if (item.kind === "folder") return "📁";
  if (item.kind === "video") return "🎬";
  if (item.kind === "subtitle") return "📝";
  const ext = extOf(item.name);
  if (ext === ".pdf") return "📕";
  if (ext === ".iso") return "💿";
  if (ext === ".nfo") return "ℹ️";
  if (AUDIO_EXT.has(ext)) return "🎵";
  if (IMAGE_EXT.has(ext)) return "🖼️";
  if (ARCHIVE_EXT.has(ext)) return "🗜️";
  if (DOC_EXT.has(ext)) return "📄";
  if (EXE_EXT.has(ext)) return "⚙️";
  return "📄";
}

function formatSize(bytes) {
  if (bytes === null || bytes === undefined || bytes < 0) return "";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const val = bytes / Math.pow(1024, i);
  return `${i === 0 ? val : val.toFixed(val < 10 ? 1 : 0)} ${units[i]}`;
}

/* ---------------------------------------------------------------------
   Clipboard — navigator.clipboard.writeText only works in a "secure
   context" (HTTPS or localhost). When this app is opened over plain
   HTTP on the LAN (e.g. http://192.168.x.x:PORT), the Clipboard API is
   unavailable entirely, so the button silently did nothing. Fall back
   to a hidden-textarea + execCommand('copy'), which works over plain
   HTTP as long as it runs inside a real user click (which it does).
   --------------------------------------------------------------------- */
async function copyText(text) {
  if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      // fall through to the legacy path below
    }
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.left = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (err) {
    return false;
  }
}

/* ---------------------------------------------------------------------
   Filters — same tri-state parent/child model as before, driven by
   the server's /api/filetypes list so extensions stay in sync.
   --------------------------------------------------------------------- */
let fileTypes = { video: [], subtitle: [] };

const videoChildrenEl = document.getElementById("videoChildren");
const subChildrenEl = document.getElementById("subChildren");

function buildChildRow(group, ext) {
  const label = document.createElement("label");
  label.className = "check-row child";
  label.innerHTML = `<input type="checkbox" data-group="${group}" data-ext="${ext}"><span class="box"></span><span class="check-label">${ext}</span>`;
  return label;
}

function buildFilterChildren() {
  fileTypes.video.forEach((e) => videoChildrenEl.appendChild(buildChildRow("video", e)));
  fileTypes.subtitle.forEach((e) => subChildrenEl.appendChild(buildChildRow("subtitle", e)));
}

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
  childCheckboxes(group).forEach((b) => (b.checked = value));
  parentCheckboxes[group].indeterminate = false;
  parentCheckboxes[group].checked = value;
}

Object.entries(parentCheckboxes).forEach(([group, el]) => {
  el.addEventListener("change", () => {
    setGroup(group, el.checked);
    updateFilterBadge();
    runSearch();
  });
});

[videoChildrenEl, subChildrenEl].forEach((container) => {
  container.addEventListener("change", (ev) => {
    const box = ev.target;
    if (box.matches('input[type="checkbox"][data-group]')) {
      syncParentFromChildren(box.dataset.group);
      updateFilterBadge();
      runSearch();
    }
  });
});

standaloneCheckboxes.folders.addEventListener("change", () => {
  updateFilterBadge();
  runSearch();
});
standaloneCheckboxes.other.addEventListener("change", () => {
  updateFilterBadge();
  runSearch();
});

document.querySelectorAll(".expand-btn[data-toggle]").forEach((btn) => {
  btn.addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    const target = btn.dataset.toggle === "video" ? videoChildrenEl : subChildrenEl;
    target.classList.toggle("show");
    btn.classList.toggle("open");
    btn.textContent = btn.classList.contains("open") ? "▾" : "▸";
  });
});

function currentFilterTokens() {
  const tokens = [];
  if (standaloneCheckboxes.folders.checked) tokens.push("folder");
  if (standaloneCheckboxes.other.checked) tokens.push("other");

  ["video", "subtitle"].forEach((group) => {
    const parent = parentCheckboxes[group];
    const boxes = childCheckboxes(group);
    const checked = boxes.filter((b) => b.checked);
    if (parent.checked && !parent.indeterminate) {
      tokens.push(group);
    } else if (checked.length > 0) {
      checked.forEach((b) => tokens.push(`${group}:${b.dataset.ext}`));
    }
  });
  return tokens;
}

/* ---------------------------------------------------------------------
   Mobile filter drawer — same checkboxes/logic, just shown as an
   off-canvas panel below the ~820px breakpoint.
   --------------------------------------------------------------------- */
const filtersEl = document.getElementById("filters");
const filterToggleBtn = document.getElementById("filterToggle");
const filtersCloseBtn = document.getElementById("filtersClose");
const applyFiltersBtn = document.getElementById("applyFilters");
const scrimEl = document.getElementById("scrim");
const filterBadgeEl = document.getElementById("filterBadge");

function openFilters() {
  filtersEl.classList.add("open");
  scrimEl.classList.add("show");
  filterToggleBtn.setAttribute("aria-expanded", "true");
}
function closeFilters() {
  filtersEl.classList.remove("open");
  scrimEl.classList.remove("show");
  filterToggleBtn.setAttribute("aria-expanded", "false");
}
filterToggleBtn.addEventListener("click", openFilters);
filtersCloseBtn.addEventListener("click", closeFilters);
scrimEl.addEventListener("click", closeFilters);
applyFiltersBtn.addEventListener("click", closeFilters);

function updateFilterBadge() {
  // "Active filter" here means the user has narrowed results below the
  // unfiltered default (nothing checked = everything shown).
  const count = currentFilterTokens().length > 0 ? 1 : 0;
  if (count > 0) {
    filterBadgeEl.hidden = false;
    filterBadgeEl.textContent = "•";
  } else {
    filterBadgeEl.hidden = true;
  }
}

/* ---------------------------------------------------------------------
   Search — only runs on Enter, not on every keystroke.
   --------------------------------------------------------------------- */
const searchInput = document.getElementById("searchInput");
const clearBtn = document.getElementById("clearBtn");
const resultsEl = document.getElementById("results");
let lastQuery = "";

searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") runSearch();
});
searchInput.addEventListener("input", () => {
  clearBtn.hidden = searchInput.value.length === 0;
});
clearBtn.addEventListener("click", () => {
  searchInput.value = "";
  clearBtn.hidden = true;
  searchInput.focus();
  runSearch();
});

async function runSearch() {
  const q = searchInput.value.trim();
  lastQuery = q;
  if (!q) {
    resultsEl.innerHTML = emptyStateHtml("Type above and press <b>Enter</b> to search your indexed media servers.");
    return;
  }
  resultsEl.innerHTML = emptyStateHtml("Searching…");

  const filterParam = currentFilterTokens().join(",");
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}&filter=${encodeURIComponent(filterParam)}`);
    const data = await resp.json();
    if (lastQuery !== q) return; // a newer search already superseded this one
    renderGroups(data.groups || []);
  } catch (err) {
    resultsEl.innerHTML = emptyStateHtml("Search failed — is the LMS server still running?", true);
  }
}

function emptyStateHtml(message, isError) {
  return `<div class="empty-state${isError ? " is-error" : ""}">
    <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <p>${message}</p>
  </div>`;
}

/* ---------------------------------------------------------------------
   Rendering — the API groups results by server, then by parent
   directory (flat, one bucket per distinct parent_path). That's the
   right shape for the database query, but rendered as-is it produces
   separate "path header" blocks for a matched folder and, right below
   it, another separate block for that folder's own matched children —
   which is confusing when both folders and files are in the result set.

   Here we reassemble those flat buckets into a real nested tree,
   client-side, using nothing but full_path/parent_path links that are
   already present in each result item. No backend/API change needed.
   --------------------------------------------------------------------- */
function displayPath(path) {
  return path === "" ? "/" : "/" + path;
}

function renderGroups(groups) {
  resultsEl.innerHTML = "";
  const totalResults = groups.reduce(
    (sum, g) => sum + g.folders.reduce((s, f) => s + f.results.length, 0),
    0
  );

  if (totalResults === 0) {
    resultsEl.innerHTML = emptyStateHtml("No matches.");
    return;
  }

  groups.forEach((group) => {
    const wrap = document.createElement("div");
    wrap.className = "server-group";

    const count = group.folders.reduce((s, f) => s + f.results.length, 0);
    const head = document.createElement("div");
    head.className = "server-head";
    head.innerHTML = `<span class="prompt">~/${escapeHtml(group.server)}</span><span class="count">${count} result${count === 1 ? "" : "s"}</span>`;
    wrap.appendChild(head);

    // Flatten every item across this server's parent_path buckets, then
    // link them back into a tree via full_path <-> parent_path.
    const allItems = [];
    group.folders.forEach((f) => f.results.forEach((r) => allItems.push(r)));

    const fullPathSet = new Set(allItems.map((i) => i.full_path));
    const childrenByParent = new Map();
    allItems.forEach((item) => {
      if (!childrenByParent.has(item.parent_path)) childrenByParent.set(item.parent_path, []);
      childrenByParent.get(item.parent_path).push(item);
    });

    // A bucket is a *root* of the tree only if nothing in the result
    // set represents that folder itself — otherwise its items belong
    // nested under that folder item, wherever it gets rendered below.
    group.folders.forEach((folder) => {
      if (fullPathSet.has(folder.path)) return;

      const rootHead = document.createElement("div");
      rootHead.className = "tree-root-head";
      rootHead.innerHTML = `<span class="folder-path">${escapeHtml(displayPath(folder.path))}</span>`;
      wrap.appendChild(rootHead);

      renderTreeItems(wrap, folder.results, [], childrenByParent);
    });

    resultsEl.appendChild(wrap);
  });
}

function renderTreeItems(container, items, ancestorIsLast, childrenByParent) {
  items.forEach((item, idx) => {
    const isLast = idx === items.length - 1;
    container.appendChild(renderRow(item, ancestorIsLast, isLast));
    const kids = childrenByParent.get(item.full_path);
    if (kids && kids.length) {
      renderTreeItems(container, kids, [...ancestorIsLast, isLast], childrenByParent);
    }
  });
}

function treePrefix(ancestorIsLast, isLast) {
  const stem = ancestorIsLast.map((last) => (last ? "   " : "│  ")).join("");
  return stem + (isLast ? "└─ " : "├─ ");
}

function renderRow(item, ancestorIsLast, isLast) {
  const row = document.createElement("div");
  row.className = "row";
  row.title = item.url;
  row.tabIndex = 0;

  const prefix = document.createElement("div");
  prefix.className = "tree-prefix";
  prefix.textContent = treePrefix(ancestorIsLast, isLast);
  row.appendChild(prefix);

  const icon = document.createElement("div");
  icon.className = "row-icon";
  icon.textContent = iconFor(item);
  row.appendChild(icon);

  const main = document.createElement("div");
  main.className = "row-main";
  const name = document.createElement("span");
  name.className =
    "row-name " + (item.kind === "folder" ? "dir" : item.kind === "video" ? "video" : item.kind === "subtitle" ? "sub" : "");
  name.textContent = item.name;
  main.appendChild(name);
  if (item.kind !== "folder" && item.size_bytes !== undefined && item.size_bytes !== null) {
    const size = document.createElement("span");
    size.className = "row-size";
    size.textContent = formatSize(item.size_bytes);
    main.appendChild(size);
  }
  row.appendChild(main);

  const actionSlot = document.createElement("div");
  if (item.kind === "video") {
    const btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.type = "button";
    btn.innerHTML = `📋 <span class="copy-label">Copy Link</span>`;
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const ok = await copyText(item.url);
      if (ok) {
        btn.classList.remove("failed");
        btn.classList.add("copied");
        btn.innerHTML = `✓ <span class="copy-label">Copied!</span>`;
      } else {
        btn.classList.remove("copied");
        btn.classList.add("failed");
        btn.innerHTML = `⚠ <span class="copy-label">Copy failed</span>`;
      }
      setTimeout(() => {
        btn.classList.remove("copied", "failed");
        btn.innerHTML = `📋 <span class="copy-label">Copy Link</span>`;
      }, 1600);
    });
    actionSlot.appendChild(btn);
  }
  row.appendChild(actionSlot);

  const open = () => window.open(item.url, "_blank", "noopener");
  row.addEventListener("click", open);
  row.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  });
  return row;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------------------------------------------------------------
   Stats + init
   --------------------------------------------------------------------- */
async function refreshStats() {
  const statsLine = document.getElementById("statsLine");
  try {
    const resp = await fetch("/api/stats");
    const s = await resp.json();
    statsLine.innerHTML =
      `<span><b>${(s.files || 0).toLocaleString()}</b> files</span>` +
      `<span><b>${(s.folders || 0).toLocaleString()}</b> folders</span>` +
      `<span><b>${s.servers || 0}</b> servers</span>`;
  } catch {
    statsLine.textContent = "Could not load index stats.";
  }
}

async function init() {
  refreshStats();
  const resp = await fetch("/api/filetypes");
  fileTypes = await resp.json();
  buildFilterChildren();
}

init();
