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
  label.innerHTML = `<input type="checkbox" data-group="${group}" data-ext="${ext}"> ${ext}`;
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
    runSearch();
  });
});

[videoChildrenEl, subChildrenEl].forEach((container) => {
  container.addEventListener("change", (ev) => {
    const box = ev.target;
    if (box.matches('input[type="checkbox"][data-group]')) {
      syncParentFromChildren(box.dataset.group);
      runSearch();
    }
  });
});

standaloneCheckboxes.folders.addEventListener("change", runSearch);
standaloneCheckboxes.other.addEventListener("change", runSearch);

document.querySelectorAll(".expand-btn[data-toggle]").forEach((btn) => {
  btn.addEventListener("click", () => {
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
   Search — only runs on Enter, not on every keystroke.
   --------------------------------------------------------------------- */
const searchInput = document.getElementById("searchInput");
const resultsEl = document.getElementById("results");
let lastQuery = "";

searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") runSearch();
});

async function runSearch() {
  const q = searchInput.value.trim();
  lastQuery = q;
  if (!q) {
    resultsEl.innerHTML = `<div class="empty-state">Type above and press Enter to search your indexed media servers.</div>`;
    return;
  }
  resultsEl.innerHTML = `<div class="empty-state">Searching…</div>`;

  const filterParam = currentFilterTokens().join(",");
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}&filter=${encodeURIComponent(filterParam)}`);
    const data = await resp.json();
    if (lastQuery !== q) return; // a newer search already superseded this one
    renderGroups(data.groups || []);
  } catch (err) {
    resultsEl.innerHTML = `<div class="empty-state">Search failed — is the LMS server still running?</div>`;
  }
}

/* ---------------------------------------------------------------------
   Rendering — server groups, each containing folder sub-groups
   (indented by nesting depth), each listing its direct results.
   --------------------------------------------------------------------- */
function depthOf(path) {
  if (!path) return 0;
  return (path.match(/\//g) || []).length;
}

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
    resultsEl.innerHTML = `<div class="empty-state">No matches.</div>`;
    return;
  }

  groups.forEach((group) => {
    const wrap = document.createElement("div");
    wrap.className = "server-group";

    const head = document.createElement("div");
    head.className = "server-head";
    const count = group.folders.reduce((s, f) => s + f.results.length, 0);
    head.innerHTML = `<span class="prompt">~/${escapeHtml(group.server)}</span><span class="count">${count} result${count === 1 ? "" : "s"}</span>`;
    wrap.appendChild(head);

    group.folders.forEach((folder) => {
      const depth = depthOf(folder.path);
      const indent = 16 + depth * 16;

      const folderHead = document.createElement("div");
      folderHead.className = "folder-head";
      folderHead.style.paddingLeft = `${indent}px`;
      folderHead.innerHTML = `<span class="tree-glyph">${"│  ".repeat(Math.max(depth - 1, 0))}${depth > 0 ? "├─ " : ""}</span><span class="folder-path">${escapeHtml(displayPath(folder.path))}</span>`;
      wrap.appendChild(folderHead);

      folder.results.forEach((item) => {
        const row = renderRow(item, indent + 12);
        wrap.appendChild(row);
      });
    });

    resultsEl.appendChild(wrap);
  });
}

function renderRow(item, indentPx) {
  const row = document.createElement("div");
  row.className = "row";
  row.style.paddingLeft = `${indentPx}px`;
  row.title = item.url;

  const icon = document.createElement("div");
  icon.className = "row-icon";
  icon.textContent = iconFor(item);
  row.appendChild(icon);

  const name = document.createElement("div");
  name.className =
    "row-name " + (item.kind === "folder" ? "dir" : item.kind === "video" ? "video" : item.kind === "subtitle" ? "sub" : "");
  name.textContent = item.name;
  row.appendChild(name);

  const actionSlot = document.createElement("div");
  if (item.kind === "video") {
    const btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.type = "button";
    btn.textContent = "📋 Copy Link";
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      navigator.clipboard.writeText(item.url).then(() => {
        btn.textContent = "Copied!";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = "📋 Copy Link";
          btn.classList.remove("copied");
        }, 1400);
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
