"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  dumps: [],
  selectedId: null,
  recording: false,
  busSource: null,
  profile: null,
  achievements: null,
  mascotState: null,
  pttJobId: null,
  pttPollTimer: null,
  traceRuns: [],
  pttTimerInterval: null,
  pttTimerStart: null,
  dumpFilter: "",
  dumpStatusFilter: "",
  dumpSort: "newest",
  theme: "auto",
};

const API = {
  health: "/api/health",
  dumps: "/api/dumps",
  dump: (id) => `/api/dumps/${id}`,
  turns: (id) => `/api/dumps/${id}/turns`,
  blueprint: (id) => `/api/dumps/${id}/blueprint`,
  ingestFake: (id) => `/api/dumps/${id}/ingest-fake`,
  status: (id) => `/api/dumps/${id}/status`,
  search: "/api/search",
  providers: "/api/providers",
  events: "/api/events",
  profile: "/api/profile",
  achievements: "/api/achievements",
  mascot: (s) => `/api/mascot/${s}.png`,
  hardwareStatus: "/api/hardware/status",
  hardwareDisplay: "/api/hardware/display",
  hardwarePisugar: "/api/hardware/pisugar",
  pttStart: "/api/hardware/ptt/start",
  pttCancel: "/api/hardware/ptt/cancel",
  pttJob: (id) => `/api/hardware/ptt/${id}`,
  storageStatus: "/api/storage/status",
  storageSync: "/api/storage/sync",
  storageExport: "/api/storage/export",
  storageImport: "/api/storage/import",
  storageRedact: "/api/storage/redact-preview",
  agentRun: "/api/agent/run",
  agentJobs: "/api/agent/jobs",
  agentJob: (id) => `/api/agent/jobs/${id}`,
  agentCancel: (id) => `/api/agent/jobs/${id}/cancel`,
  agentRetry: (id) => `/api/agent/jobs/${id}/retry`,
  agentHealth: "/api/agent/health",
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text || res.statusText}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------------------------------------------------------------------------
// P0-1: Hand-rolled markdown renderer (headings, lists, bold, inline code,
//        fenced code blocks). No CDN dependency.
// ---------------------------------------------------------------------------

function escHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function inlineFormat(str) {
  // Bold: **text**
  str = str.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  // Inline code: `text`
  str = str.replace(/`([^`]+)`/g, (_, c) => `<code>${escHtml(c)}</code>`);
  return str;
}

function renderMarkdown(md) {
  if (!md) return "";
  const lines = md.split("\n");
  let html = "";
  let inUl = false;
  let inOl = false;
  let inCode = false;
  let codeBuffer = "";
  let codeLang = "";

  function closeList() {
    if (inUl) { html += "</ul>"; inUl = false; }
    if (inOl) { html += "</ol>"; inOl = false; }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Fenced code blocks
    const fence = line.match(/^```(\w*)/);
    if (fence) {
      if (inCode) {
        html += escHtml(codeBuffer) + "</code></pre>";
        inCode = false;
        codeBuffer = "";
        codeLang = "";
      } else {
        closeList();
        codeLang = fence[1] || "";
        html += codeLang
          ? `<pre><code class="language-${codeLang}">`
          : "<pre><code>";
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuffer += (codeBuffer ? "\n" : "") + line;
      continue;
    }

    // ATX headings
    const h3m = line.match(/^### (.+)/);
    const h2m = line.match(/^## (.+)/);
    const h1m = line.match(/^# (.+)/);
    if (h1m) { closeList(); html += `<h1>${inlineFormat(h1m[1])}</h1>`; continue; }
    if (h2m) { closeList(); html += `<h2>${inlineFormat(h2m[1])}</h2>`; continue; }
    if (h3m) { closeList(); html += `<h3>${inlineFormat(h3m[1])}</h3>`; continue; }

    // Unordered list item
    const ulItem = line.match(/^[-*] (.+)/);
    if (ulItem) {
      if (inOl) { html += "</ol>"; inOl = false; }
      if (!inUl) { html += "<ul>"; inUl = true; }
      html += `<li>${inlineFormat(ulItem[1])}</li>`;
      continue;
    }

    // Ordered list item
    const olItem = line.match(/^\d+\. (.+)/);
    if (olItem) {
      if (inUl) { html += "</ul>"; inUl = false; }
      if (!inOl) { html += "<ol>"; inOl = true; }
      html += `<li>${inlineFormat(olItem[1])}</li>`;
      continue;
    }

    // Blank line
    if (!line.trim()) {
      closeList();
      continue;
    }

    // Plain paragraph
    closeList();
    html += `<p>${inlineFormat(line)}</p>`;
  }

  closeList();
  if (inCode) html += escHtml(codeBuffer) + "</code></pre>";
  return html;
}

// ---------------------------------------------------------------------------
// UI-02: Section chips — horizontal scrollable TOC from ## headings
// ---------------------------------------------------------------------------

function buildSectionChips(md, mdBodyEl) {
  const sections = [];
  for (const line of md.split("\n")) {
    const m = line.match(/^## (.+)/);
    if (m) sections.push(m[1]);
  }
  if (!sections.length) return null;

  const wrap = document.createElement("div");
  wrap.className = "section-chips";

  const countChip = document.createElement("span");
  countChip.className = "section-chip section-chip--count";
  countChip.textContent = `${sections.length}/12 sections`;
  wrap.appendChild(countChip);

  for (const title of sections) {
    const chip = document.createElement("button");
    chip.className = "section-chip";
    chip.type = "button";
    chip.textContent = title;
    if (/\bTBD\b/i.test(title)) chip.classList.add("section-chip--muted");
    chip.addEventListener("click", () => {
      if (!mdBodyEl) return;
      const hs = Array.from(mdBodyEl.querySelectorAll(".md-body h2"));
      const stripped = title.replace(/^\d+\.\s*/, "").toLowerCase();
      const target = hs.find((h) => h.textContent.toLowerCase().includes(stripped));
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    wrap.appendChild(chip);
  }
  return wrap;
}

// ---------------------------------------------------------------------------
// P0-4: Empty state helper — Dumpi image + message + optional CTA button
// ---------------------------------------------------------------------------

function createEmptyState(message, ctaText, ctaAction) {
  const div = document.createElement("div");
  div.className = "empty-state";

  const img = document.createElement("img");
  img.src = "/api/mascot/idle.png";
  img.alt = "Dumpi";
  img.className = "empty-state-img";

  const p = document.createElement("p");
  p.className = "empty-state-msg";
  p.textContent = message;

  div.appendChild(img);
  div.appendChild(p);

  if (ctaText && ctaAction) {
    const btn = document.createElement("button");
    btn.className = "btn primary";
    btn.type = "button";
    btn.textContent = ctaText;
    btn.addEventListener("click", ctaAction);
    div.appendChild(btn);
  }
  return div;
}

// ---------------------------------------------------------------------------
// P0-5: Toast with level — "error" toasts are sticky with a close button
// ---------------------------------------------------------------------------

function showToast(text, level = "info") {
  const log = $("#eventLog");
  const t = document.createElement("div");
  t.className = "toast";
  if (level === "error") t.classList.add("toast--error");
  if (level === "warn") t.classList.add("toast--warn");

  const msg = document.createElement("span");
  msg.textContent = text;
  t.appendChild(msg);

  if (level === "error") {
    // Sticky: no auto-dismiss, show close button
    const closeBtn = document.createElement("button");
    closeBtn.className = "toast-close";
    closeBtn.setAttribute("aria-label", "Close notification");
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => t.remove());
    t.appendChild(closeBtn);
    t.style.pointerEvents = "auto";
  } else {
    setTimeout(() => t.remove(), 5000);
  }

  log.appendChild(t);
}

// ---------------------------------------------------------------------------
// P1-6: SSE connection indicator
// ---------------------------------------------------------------------------

function setSSEStatus(status) {
  const dot = $("#sseIndicator");
  if (!dot) return;
  dot.dataset.status = status;
  const labels = { connected: "Live", connecting: "Connecting…", error: "Disconnected" };
  dot.setAttribute("aria-label", `Event stream: ${labels[status] || status}`);
  dot.title = labels[status] || status;
}

// ---------------------------------------------------------------------------
// P1-2: Mascot typewriter animation
// ---------------------------------------------------------------------------

let _typewriterTimer = null;

function typewriterText(el, text, speed = 28) {
  if (_typewriterTimer) {
    clearInterval(_typewriterTimer);
    _typewriterTimer = null;
  }
  let i = 0;
  el.textContent = "";
  if (!text) return;
  _typewriterTimer = setInterval(() => {
    i++;
    el.textContent = text.slice(0, i);
    if (i >= text.length) {
      clearInterval(_typewriterTimer);
      _typewriterTimer = null;
    }
  }, speed);
}

// ---------------------------------------------------------------------------
// P0-3: Focus-trap helpers for drawers
// ---------------------------------------------------------------------------

let _focusTrapReturnEl = null;

const FOCUSABLE_SELECTORS = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(", ");

function getFocusableElements(container) {
  return Array.from(container.querySelectorAll(FOCUSABLE_SELECTORS)).filter(
    (el) => !el.closest("[hidden]") && el.offsetParent !== null
  );
}

function relativeTime(iso) {
  const then = new Date(iso.includes("Z") ? iso : `${iso}Z`).getTime();
  const diff = Date.now() - then;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(then).toLocaleDateString();
}

function applyTheme(mode) {
  state.theme = mode;
  const root = document.documentElement;
  if (mode === "auto") {
    root.removeAttribute("data-theme");
    localStorage.removeItem("vibedump-theme");
  } else {
    root.dataset.theme = mode;
    localStorage.setItem("vibedump-theme", mode);
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = mode === "light" ? "#f4f6fb" : "#0b0f1a";
}

function initTheme() {
  const saved = localStorage.getItem("vibedump-theme");
  const mode = saved || "auto";
  applyTheme(mode);
  const sel = $("#themeToggle");
  if (sel) sel.value = mode;
}

function showConfirmSheet(message, title = "Delete dump?") {
  return new Promise((resolve) => {
    const sheet = $("#confirmSheet");
    const body = $("#confirmBody");
    const ok = $("#confirmOk");
    const cancel = $("#confirmCancel");
    if (!sheet || !body || !ok || !cancel) {
      resolve(window.confirm(message));
      return;
    }
    $("#confirmTitle").textContent = title;
    body.textContent = message;
    sheet.hidden = false;
    const cleanup = (val) => {
      sheet.hidden = true;
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      resolve(val);
    };
    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
    cancel.focus();
  });
}

function showAchievementOverlay(title, description) {
  const overlay = $("#achievementOverlay");
  const t = $("#achievementTitle");
  const d = $("#achievementDesc");
  const btn = $("#achievementDismiss");
  if (!overlay || !t || !d) return;
  t.textContent = title || "Achievement unlocked!";
  d.textContent = description || "";
  overlay.hidden = false;
  const close = () => { overlay.hidden = true; };
  btn?.addEventListener("click", close, { once: true });
  setTimeout(close, 5000);
}

function getFilteredDumps() {
  let items = [...state.dumps];
  const q = state.dumpFilter.trim().toLowerCase();
  if (q) items = items.filter((d) => d.title.toLowerCase().includes(q));
  if (state.dumpStatusFilter) {
    items = items.filter((d) => d.status === state.dumpStatusFilter);
  }
  if (state.dumpSort === "oldest") {
    items.sort((a, b) => a.created_at.localeCompare(b.created_at));
  } else if (state.dumpSort === "title") {
    items.sort((a, b) => a.title.localeCompare(b.title));
  } else {
    items.sort((a, b) => b.created_at.localeCompare(a.created_at));
  }
  return items;
}

function setMascotState(s) {
  const mascot = $("#mascot");
  const chip = $("#statusChip");
  const line = $("#mascotLine");
  const img = $("#mascotImg");
  mascot.dataset.state = s;
  chip.dataset.state = s;
  chip.textContent = s.charAt(0).toUpperCase() + s.slice(1);
  if (img && state.mascotState !== s) {
    img.src = API.mascot(s);
    state.mascotState = s;
  }
  const lines = {
    idle:      "Tap record to dump your next messy idea.",
    listening: "I'm listening… keep going.",
    thinking:  "Cooking up a spec…",
    speaking:  "Here's what I heard.",
    error:     "Oof, something broke. Check the log.",
    level_up:  "Level up! 🎉",
    sleeping:  "Zzz…",
  };
  // P1-2: animate the line text with a typewriter effect on state change
  if (line) typewriterText(line, lines[s] || "");
}

function renderDumps() {
  const list = $("#dumpList");
  list.innerHTML = "";
  if (state.dumps.length === 0) {
    list.appendChild(
      createEmptyState(
        "No dumps yet. Tap the mic to start.",
        "🎙 New dump",
        () => openDrawer($("#settingsDrawer"))
      )
    );
    return;
  }
  const visible = getFilteredDumps();
  if (visible.length === 0 && state.dumps.length > 0) {
    list.appendChild(createEmptyState("No dumps match your filters.", null, null));
    return;
  }
  for (const dump of visible) {
    const btn = document.createElement("button");
    btn.className = "dump-item" + (dump.id === state.selectedId ? " is-active" : "");
    btn.type = "button";
    btn.innerHTML = `
      <div class="title"></div>
      <div class="meta">
        <span class="status-chip-sm" data-status=""></span>
        <span class="ts"></span>
      </div>`;
    btn.querySelector(".title").textContent = dump.title;
    const badge = btn.querySelector(".status-chip-sm");
    badge.dataset.status = dump.status;
    badge.textContent = dump.status;
    btn.querySelector(".ts").textContent = relativeTime(dump.created_at);
    btn.title = new Date(dump.created_at + "Z").toLocaleString();
    btn.addEventListener("click", () => selectDump(dump.id));
    list.appendChild(btn);
  }
}

function showDumpListSkeleton() {
  const list = $("#dumpList");
  if (!list) return;
  list.innerHTML = "";
  for (let i = 0; i < 3; i++) {
    const sk = document.createElement("div");
    sk.className = "skeleton";
    sk.setAttribute("aria-hidden", "true");
    list.appendChild(sk);
  }
}

async function loadDumps() {
  const list = $("#dumpList");
  showDumpListSkeleton();
  try {
    const data = await api(API.dumps);
    state.dumps = data.items;
    renderDumps();
  } catch (e) {
    list.innerHTML = "";
    const errDiv = createEmptyState(`Failed to load dumps: ${e.message}`, "↻ Retry", loadDumps);
    errDiv.querySelector("button").id = "dumpsRetryBtn";
    list.appendChild(errDiv);
  }
}

async function selectDump(id) {
  state.selectedId = id;
  renderDumps();
  const detail = $("#dumpDetail");
  detail.classList.remove("empty");
  detail.innerHTML = `<div class="empty">Loading dump ${id}…</div>`;
  try {
    const [turnsRes, blueprintRes] = await Promise.all([
      api(API.turns(id)),
      api(API.blueprint(id)).catch((e) => ({ error: e.message })),
    ]);
    renderDumpDetail(detail, turnsRes, blueprintRes);
  } catch (e) {
    detail.innerHTML = `<div class="empty">Failed to load: ${e.message}</div>`;
  }
}

function renderDumpDetail(root, turnsRes, blueprintRes) {
  root.innerHTML = "";

  // UI-09: back button shown on mobile (<900px)
  const backBtn = document.createElement("button");
  backBtn.className = "btn detail-back-btn";
  backBtn.id = "detailBackBtn";
  backBtn.type = "button";
  backBtn.textContent = "← Back";
  backBtn.addEventListener("click", () => {
    if (window.innerWidth < 900) {
      state.selectedId = null;
      renderDumps();
      root.className = "empty";
      root.textContent = "Pick a dump to see transcript + blueprint.";
    }
  });
  root.appendChild(backBtn);

  const transcript = document.createElement("div");
  transcript.className = "chat-thread";
  const transcriptHeading = document.createElement("h3");
  transcriptHeading.textContent = "🗣 Transcript";
  root.appendChild(transcriptHeading);
  root.appendChild(transcript);
  const profileName = (state.profile && state.profile.name) || "You";
  if (!turnsRes.items || turnsRes.items.length === 0) {
    transcript.appendChild(
      createEmptyState(
        "No turns yet. Tap ingest-fake to add a sample turn.",
        null, null
      )
    );
  } else {
    for (const t of turnsRes.items) {
      const bubble = document.createElement("div");
      const isUser = t.role === "user";
      bubble.className = `chat-bubble chat-bubble--${isUser ? "user" : "assistant"}`;
      const avatar = document.createElement("div");
      avatar.className = "chat-avatar";
      avatar.textContent = isUser ? "🧑" : "💩";
      const bodyWrap = document.createElement("div");
      const body = document.createElement("div");
      body.className = "chat-body";
      body.textContent = t.text;
      const meta = document.createElement("div");
      meta.className = "chat-meta";
      meta.textContent = `${isUser ? profileName : "Dumpi"} · ${relativeTime(t.created_at || new Date().toISOString())}`;
      bodyWrap.append(body, meta);
      bubble.append(avatar, bodyWrap);
      transcript.appendChild(bubble);
    }
  }

  const bp = document.createElement("div");
  bp.className = "blueprint-view";
  const bpHeading = document.createElement("h3");
  bpHeading.textContent = "💎 Blueprint";
  root.appendChild(bpHeading);
  root.appendChild(bp);
  if (blueprintRes.error) {
    bp.appendChild(
      createEmptyState(
        "No blueprint yet. Hit ingest-fake below.",
        null, null
      )
    );
  } else {
    // P0-1: render blueprint markdown instead of raw <pre>
    const mdDiv = document.createElement("div");
    mdDiv.className = "md-body";
    mdDiv.innerHTML = renderMarkdown(blueprintRes.markdown);
    // UI-02: section chips before the markdown body
    const chips = buildSectionChips(blueprintRes.markdown, mdDiv);
    if (chips) bp.appendChild(chips);
    bp.appendChild(mdDiv);
    const copyBtn = document.createElement("button");
    copyBtn.className = "btn";
    copyBtn.textContent = "📋 Copy markdown";
    copyBtn.addEventListener("click", () =>
      navigator.clipboard.writeText(blueprintRes.markdown)
    );
    bp.appendChild(copyBtn);
  }

  const actions = document.createElement("div");
  actions.className = "row-actions";
  const ingestBtn = document.createElement("button");
  ingestBtn.className = "btn primary";
  ingestBtn.textContent = "✨ Ingest fake turn";
  ingestBtn.addEventListener("click", async () => {
    try {
      await api(API.ingestFake(state.selectedId), { method: "POST" });
      await selectDump(state.selectedId);
      await loadDumps();
      showToast("Blueprint generated");
    } catch (e) { showToast(`Ingest failed: ${e.message}`, "error"); }
  });
  const delBtn = document.createElement("button");
  delBtn.className = "btn";
  delBtn.textContent = "🗑 Delete";
  delBtn.addEventListener("click", async () => {
    const ok = await showConfirmSheet("This dump and its blueprint will be permanently removed.", "Delete dump?");
    if (!ok) return;
    try {
      await api(API.dump(state.selectedId), { method: "DELETE" });
      state.selectedId = null;
      $("#dumpDetail").className = "empty";
      $("#dumpDetail").textContent = "Pick a dump to see transcript + blueprint.";
      await loadDumps();
      showToast("Dump deleted");
    } catch (e) { showToast(`Delete failed: ${e.message}`, "error"); }
  });
  actions.append(ingestBtn, delBtn);
  root.appendChild(actions);
}

async function loadProviders() {
  const data = await api(API.providers);
  const list = $("#healthList");
  const nav = $("#navHealth");
  list.innerHTML = "";
  if (!data.health || data.health.length === 0) {
    nav.textContent = "No providers yet";
    return;
  }
  nav.textContent = `${data.health.length} providers loaded`;
  // UI-12: build name→config map for kind badge
  const configMap = {};
  for (const c of (data.configs || [])) configMap[c.name] = c;
  for (const h of data.health) {
    const row = document.createElement("div");
    row.className = "health-row";
    row.dataset.ok = String(h.ok);
    const kind = (configMap[h.name] && configMap[h.name].kind) || "";
    const statusText = h.ok ? "ok" : "error";
    row.innerHTML = `<span class="health-dot"></span>${kind ? `<span class="kind-badge kind-badge--${kind}">${kind.toUpperCase()}</span>` : ""}<span class="name"></span><span class="health-status health-status--${statusText}">${statusText}</span><span class="detail"></span>`;
    row.querySelector(".name").textContent = h.name;
    row.querySelector(".detail").textContent = h.detail || "";
    list.appendChild(row);
  }
  return data;
}

async function loadProfile() {
  try {
    const p = await api(API.profile);
    state.profile = p;
    renderProfile();
  } catch (e) {
    showToast(`Profile load failed: ${e.message}`, "warn");
  }
}

function renderProfile() {
  const p = state.profile;
  if (!p) return;
  const pill = $("#levelPill");
  if (pill) pill.textContent = `Lvl ${p.level} · ${p.xp} XP`;
  const name = $("#profileName");
  if (name) name.textContent = p.name || "(unnamed)";
  const xpInLevel = p.xp % 100;
  const fill = $("#xpFill");
  if (fill) fill.style.width = `${xpInLevel}%`;
  const info = $("#xpInfo");
  if (info) {
    info.textContent = `Lvl ${p.level} · ${p.xp} XP · ${p.xp_to_next_level} to next · 🔥 ${p.streak_days}d streak`;
  }
  // UI-11: mascot panel XP bar + streak
  const mascotXp = $("#mascotXp");
  const mascotXpFill = $("#mascotXpFill");
  const mascotStreak = $("#mascotStreak");
  if (mascotXp) {
    if (mascotXpFill) mascotXpFill.style.width = `${xpInLevel}%`;
    if (mascotStreak) mascotStreak.textContent = `🔥 ${p.streak_days}d streak`;
    mascotXp.hidden = false;
  }
}

async function loadAchievements() {
  try {
    const data = await api(API.achievements);
    state.achievements = data;
    renderAchievements();
  } catch (e) {
    showToast(`Achievements load failed: ${e.message}`, "warn");
  }
}

function renderAchievements() {
  const list = $("#achList");
  if (!list) return;
  const data = state.achievements;
  list.innerHTML = "";
  if (!data || !data.items || data.items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No achievements configured yet.";
    list.appendChild(empty);
    return;
  }
  for (const a of data.items) {
    const row = document.createElement("div");
    const unlocked = a.unlocked_at != null;
    row.className = "ach-row" + (unlocked ? "" : " is-locked");
    if (a._justUnlocked) row.classList.add("is-new");
    row.innerHTML = `<div class="ach-title"></div><div class="ach-desc"></div>`;
    row.querySelector(".ach-title").textContent = unlocked ? `🏆 ${a.title}` : `🔒 ${a.title}`;
    row.querySelector(".ach-desc").textContent = a.description || "";
    list.appendChild(row);
  }
  const summary = document.createElement("div");
  summary.className = "xp-info";
  summary.textContent = `${data.unlocked_count} / ${data.total} unlocked`;
  list.appendChild(summary);
}

async function loadDefaultLlmOptions() {
  const select = $("#defaultLlm");
  if (!select) return;
  select.innerHTML = "";
  const envOpt = document.createElement("option");
  envOpt.value = "__env__";
  envOpt.textContent = "Use env override (VIBEDUMP_LLM_PROVIDER)";
  select.appendChild(envOpt);
  let llmNames = [];
  try {
    const data = await api(API.providers);
    const configs = (data.configs || []).filter((c) => c.enabled);
    const llmConfigs = configs.filter((c) => c.kind === "llm");
    llmNames = llmConfigs.length > 0
      ? llmConfigs.map((c) => c.name)
      : configs.map((c) => c.name);
    for (const name of llmNames) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
    if (llmNames.length === 0) {
      const none = document.createElement("option");
      none.disabled = true;
      none.textContent = "(no providers configured)";
      select.appendChild(none);
    }
  } catch (e) {
    showToast(`LLM options load failed: ${e.message}`, "warn");
  }
  const saved = localStorage.getItem("vibedump.defaultLlm");
  if (saved && (saved === "__env__" || llmNames.includes(saved))) {
    select.value = saved;
  }
}

async function saveDefaultLlm() {
  const select = $("#defaultLlm");
  if (!select) return;
  const value = select.value;
  try {
    if (value && value !== "__env__") {
      await api(API.providers, {
        method: "POST",
        body: JSON.stringify({ name: value, kind: "llm", enabled: true, config: {} }),
      });
    }
    localStorage.setItem("vibedump.defaultLlm", value || "__env__");
    showToast(value && value !== "__env__" ? `Default LLM set to ${value}` : "Default LLM set to env override");
    loadProviders();
  } catch (e) {
    showToast(`Save failed: ${e.message}`, "error");
  }
}

async function saveProfileName() {
  const input = $("#newProfileName");
  if (!input) return;
  const name = input.value.trim();
  if (!name) return showToast("Name cannot be blank", "warn");
  try {
    const updated = await api(API.profile, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
    state.profile = updated;
    renderProfile();
    input.value = "";
    showToast("Name updated");
  } catch (e) {
    showToast(`Update failed: ${e.message}`, "error");
  }
}

async function runSearch(q) {
  if (!q) return;
  try {
    const data = await api(`${API.search}?q=${encodeURIComponent(q)}`);
    const list = $("#dumpList");
    list.innerHTML = "";
    const heading = document.createElement("h2");
    heading.textContent = `🔍 ${data.results.length} matches`;
    heading.style.margin = "0 0 12px";
    heading.style.fontSize = "0.9rem";
    list.appendChild(heading);
    for (const r of data.results) {
      const btn = document.createElement("button");
      btn.className = "dump-item";
      btn.type = "button";
      btn.innerHTML = `<div class="title"></div><div class="meta"><span class="badge"></span></div>`;
      btn.querySelector(".title").textContent = `${r.title} · dump #${r.dump_id}`;
      btn.querySelector(".badge").textContent = `score ${r.score.toFixed(2)}`;
      btn.addEventListener("click", () => selectDump(r.dump_id));
      list.appendChild(btn);
    }
  } catch (e) { showToast(`Search failed: ${e.message}`, "warn"); }
}

// P0-3: openDrawer / closeDrawers with focus trap and focus restore
function openDrawer(el) {
  _focusTrapReturnEl = document.activeElement;
  $("#scrim").classList.add("is-open");
  el.classList.add("is-open");
  // Move focus into the drawer after the CSS transition starts
  requestAnimationFrame(() => {
    const focusable = getFocusableElements(el);
    if (focusable.length) focusable[0].focus();
  });
}

function closeDrawers() {
  const returnEl = _focusTrapReturnEl;
  _focusTrapReturnEl = null;
  $("#scrim").classList.remove("is-open");
  $$(".drawer").forEach((d) => d.classList.remove("is-open"));
  if (returnEl && typeof returnEl.focus === "function") {
    returnEl.focus();
  }
}

function connectSSE() {
  setSSEStatus("connecting");
  if (state.busSource) state.busSource.close();
  const es = new EventSource(API.events);
  state.busSource = es;
  es.onopen = () => setSSEStatus("connected");
  es.addEventListener("dump.created", (e) => {
    const data = JSON.parse(e.data).payload;
    showToast(`New dump #${data.dump_id}: ${data.title}`);
    loadDumps();
  });
  es.addEventListener("dump.deleted", () => { loadDumps(); });
  es.addEventListener("blueprint.generated", (e) => {
    const data = JSON.parse(e.data).payload;
    showToast(`Blueprint ready for #${data.dump_id}`);
    if (data.dump_id === state.selectedId) selectDump(data.dump_id);
  });
  es.addEventListener("provider.updated", (e) => {
    const data = JSON.parse(e.data).payload;
    showToast(`Provider ${data.name} saved`);
    loadProviders();
  });
  es.addEventListener("profile.xp", (e) => {
    const data = JSON.parse(e.data).payload;
    if (state.profile) {
      state.profile = {
        ...state.profile,
        xp: data.xp ?? state.profile.xp,
        level: data.level ?? state.profile.level,
        xp_to_next_level: data.xp_to_next_level ?? state.profile.xp_to_next_level,
      };
      renderProfile();
    }
    if (data.delta) showToast(`+${data.delta} XP`);
  });
  es.addEventListener("profile.level_up", (e) => {
    const data = JSON.parse(e.data).payload;
    if (state.profile) {
      state.profile = { ...state.profile, level: data.level ?? state.profile.level };
      renderProfile();
    }
    setMascotState("level_up");
    showToast("Level up! 🎉");
  });
  es.addEventListener("achievement.unlocked", (e) => {
    const data = JSON.parse(e.data).payload;
    const title = data.title || data.key || "Achievement";
    showToast(`🏆 ${title} unlocked`);
    showAchievementOverlay(title, data.description || "");
    loadAchievements();
  });
  es.addEventListener("ptt.completed", (e) => {
    const data = JSON.parse(e.data).payload || {};
    const transcript = (data.transcript || "").toString().trim();
    const status = data.status || "done";
    if (status === "error") {
      showToast(`🎙 PTT failed: ${data.error || "unknown error"}`, "error");
    } else if (transcript) {
      showToast(`🎙 "${transcript}"`);
    } else {
      showToast("🎙 PTT captured nothing");
    }
    stopPttPolling();
    stopPttTimer();
    state.pttJobId = null;
    const pttBtn = $("#pttHoldBtn");
    if (pttBtn) {
      pttBtn.classList.remove("is-ptt-active");
      pttBtn.setAttribute("aria-pressed", "false");
    }
    if (state.selectedId) selectDump(state.selectedId);
  });
  es.addEventListener("storage.sync_ok", () => {
    showToast("☁ Sync complete");
    loadStorageStatus();
  });
  es.addEventListener("storage.sync_failed", (e) => {
    const data = JSON.parse(e.data).payload || {};
    showToast(`☁ Sync failed: ${data.error || "unknown error"}`, "error");
    loadStorageStatus();
  });
  es.addEventListener("storage.export_ok", (e) => {
    const data = JSON.parse(e.data).payload || {};
    showToast(`📦 Exported bundle (${data.size_bytes || 0} B)`);
    loadStorageStatus();
  });
  es.addEventListener("storage.import_ok", (e) => {
    const data = JSON.parse(e.data).payload || {};
    const m = data.manifest || {};
    showToast(`📥 Imported ${m.dump_count || 0} dumps`);
    loadStorageStatus();
    loadDumps();
  });
  es.addEventListener("agent.job_enqueued", (e) => {
    const data = JSON.parse(e.data).payload || {};
    showToast(`🤖 Job ${(data.job_id || "").slice(0, 8)} enqueued (${data.kind || "oneshot"})`);
    loadAgentJobs();
    loadAgentHealth();
  });
  es.addEventListener("agent.job_cancelled", (e) => {
    const data = JSON.parse(e.data).payload || {};
    showToast(`🤖 Job ${(data.job_id || "").slice(0, 8)} cancelled`);
    loadAgentJobs();
    loadAgentHealth();
  });
  es.addEventListener("agent.job_completed", (e) => {
    const data = JSON.parse(e.data).payload || {};
    showToast(`🤖 Job ${(data.job_id || "").slice(0, 8)} completed`);
    loadAgentJobs();
    loadAgentHealth();
  });
  es.addEventListener("agent.job_failed", (e) => {
    const data = JSON.parse(e.data).payload || {};
    showToast(`🤖 Job ${(data.job_id || "").slice(0, 8)} failed: ${data.error || "unknown"}`, "error");
    loadAgentJobs();
    loadAgentHealth();
  });
  es.addEventListener("agent.trace", (e) => {
    try {
      const data = JSON.parse(e.data).payload || {};
      state.traceRuns = (state.traceRuns || []).concat([data]).slice(-50);
      renderTracePanel();
    } catch (_) {
      // Telemetry is best-effort; never break SSE handling.
    }
  });
  // M10-02: Swarm events
  es.addEventListener("swarm.started", (e) => {
    try {
      const data = JSON.parse(e.data).payload || {};
      showToast(`🐝 Swarm ${(data.swarm_id || "").slice(0, 8)} started`);
      renderSwarmNode({ type: "started", ...data });
    } catch (_) {}
  });
  es.addEventListener("swarm.node_done", (e) => {
    try {
      const data = JSON.parse(e.data).payload || {};
      renderSwarmNode({ type: "node_done", ...data });
    } catch (_) {}
  });
  es.addEventListener("swarm.completed", (e) => {
    try {
      const data = JSON.parse(e.data).payload || {};
      showToast("🐝 Swarm completed");
      renderSwarmNode({ type: "completed", ...data });
    } catch (_) {}
  });
  es.onerror = () => {
    setSSEStatus("error");
    setTimeout(connectSSE, 3000);
  };
}

function stopPttPolling() {
  if (state.pttPollTimer) {
    clearInterval(state.pttPollTimer);
    state.pttPollTimer = null;
  }
}

// UI-03/04: Recording timer helpers
function startPttTimer() {
  stopPttTimer();
  state.pttTimerStart = Date.now();
  const timerEl = $("#pttTimer");
  if (timerEl) timerEl.hidden = false;
  state.pttTimerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - state.pttTimerStart) / 1000);
    const m = Math.floor(elapsed / 60);
    const s = elapsed % 60;
    const el = $("#pttTimer");
    if (el) el.textContent = `${m}:${String(s).padStart(2, "0")}`;
  }, 250);
}

function stopPttTimer() {
  if (state.pttTimerInterval) {
    clearInterval(state.pttTimerInterval);
    state.pttTimerInterval = null;
  }
  state.pttTimerStart = null;
  const timerEl = $("#pttTimer");
  if (timerEl) timerEl.hidden = true;
}

function startPttPolling(jobId) {
  stopPttPolling();
  state.pttPollTimer = setInterval(async () => {
    try {
      const job = await api(API.pttJob(jobId));
      if (job.status === "done" || job.status === "error" || job.status === "cancelled") {
        stopPttPolling();
        const transcript = (job.transcript || "").toString().trim();
        if (job.status === "error") {
          showToast(`🎙 PTT failed: ${job.error || "unknown error"}`, "error");
        } else if (transcript) {
          showToast(`🎙 "${transcript}"`);
        } else {
          showToast("🎙 PTT captured nothing");
        }
        stopPttTimer();
        state.pttJobId = null;
        const pttBtn = $("#pttHoldBtn");
        if (pttBtn) {
          pttBtn.classList.remove("is-ptt-active");
          pttBtn.setAttribute("aria-pressed", "false");
        }
        if (state.selectedId) selectDump(state.selectedId);
      }
    } catch (e) {
      stopPttPolling();
      stopPttTimer();
    }
  }, 500);
}

async function loadAgentHealth() {
  const dot = $("#agentDaemonDot");
  const label = $("#agentDaemonLabel");
  const counts = $("#agentCountsLabel");
  const tools = $("#agentToolsList");
  const err = $("#agentRuntimeError");
  try {
    const data = await api(API.agentHealth);
    if (dot) dot.style.background = data.daemon ? "var(--accent-good)" : "var(--accent-bad)";
    if (label) label.textContent = data.daemon ? "daemon running" : "daemon stopped";
    if (counts) {
      counts.textContent = `pending ${data.queue_pending} · claimed ${data.queue_claimed}`;
    }
    if (tools) {
      tools.textContent = (data.tools || []).length > 0
        ? (data.tools || []).join(", ")
        : "(no tools registered)";
    }
    if (err) {
      if (data.runtime_error) {
        err.style.display = "block";
        err.textContent = data.runtime_error;
      } else {
        err.style.display = "none";
        err.textContent = "";
      }
    }
    return data;
  } catch (e) {
    if (dot) dot.style.background = "var(--accent-bad)";
    if (label) label.textContent = `agent unavailable: ${e.message}`;
    if (counts) counts.textContent = "—";
    if (tools) tools.textContent = "—";
    return null;
  }
}

async function loadAgentJobs() {
  const root = $("#agentJobList");
  if (!root) return;
  try {
    const data = await api(API.agentJobs);
    renderAgentJobs(data.items || []);
  } catch (e) {
    root.innerHTML = "";
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = `Jobs unavailable: ${e.message}`;
    root.appendChild(empty);
  }
}

function renderTracePanel() {
  const root = $("#traceList");
  if (!root) return;
  const runs = state.traceRuns || [];
  root.innerHTML = "";
  if (!runs.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No agent runs yet.";
    root.appendChild(empty);
    return;
  }
  for (let i = runs.length - 1; i >= 0; i--) {
    const r = runs[i];
    const row = document.createElement("div");
    row.className = "ach-row";
    const title = document.createElement("div");
    title.className = "ach-title";
    title.textContent = `${r.model || "agent"} · ${r.steps || 0} steps · ${r.tool_call_count || 0} tools · ${r.duration_ms || 0} ms`;
    const desc = document.createElement("div");
    desc.className = "ach-desc";
    const preview = (r.final_message_preview || "").toString().replace(/\s+/g, " ").trim();
    desc.textContent = preview ? preview.slice(0, 120) : "(no final message)";
    row.appendChild(title);
    row.appendChild(desc);
    if (r.tool_calls && r.tool_calls.length) {
      const toolsLine = document.createElement("div");
      toolsLine.className = "ach-desc";
      toolsLine.textContent = "tools: " + r.tool_calls.map((t) => t.name).join(", ");
      row.appendChild(toolsLine);
    }
    root.appendChild(row);
  }
}

// M10-02: Render a swarm event into the swarm panel
function renderSwarmNode(event) {
  const list = $("#swarmNodeList");
  if (!list) return;
  const empty = list.querySelector(".empty");
  if (empty) empty.remove();
  const row = document.createElement("div");
  row.className = "swarm-node-row";
  const icons = { started: "🐝", node_done: "✅", completed: "🎉" };
  const title = document.createElement("div");
  title.style.fontWeight = "600";
  title.textContent = `${icons[event.type] || "·"} ${event.type}${event.node_id ? ` · ${event.node_id}` : ""}`;
  const desc = document.createElement("div");
  desc.style.color = "var(--text-muted)";
  desc.textContent = event.result
    ? String(event.result).slice(0, 80)
    : (event.swarm_id || "").slice(0, 16);
  row.appendChild(title);
  row.appendChild(desc);
  list.insertBefore(row, list.firstChild);
}

function renderAgentJobs(jobs) {
  const root = $("#agentJobList");
  if (!root) return;
  root.innerHTML = "";
  if (!jobs || jobs.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No agent jobs yet.";
    root.appendChild(empty);
    return;
  }
  for (const job of jobs) {
    const row = document.createElement("div");
    row.className = "agent-job-row";
    row.dataset.status = job.status;
    const title = document.createElement("div");
    title.className = "ach-title";
    title.textContent = `${job.status} · ${job.kind} · ${(job.prompt || "").slice(0, 60)}`;
    const desc = document.createElement("div");
    desc.className = "ach-desc";
    const when = job.completed_at || job.claimed_at || job.created_at || "";
    desc.textContent = `id ${job.id.slice(0, 8)} · attempts ${job.attempts}/${job.max_attempts} · ${relativeTime(when || new Date().toISOString())}`;
    const detail = document.createElement("div");
    detail.className = "agent-job-detail";
    detail.hidden = true;
    detail.textContent = [
      job.prompt || "",
      job.result ? `\n\nResult:\n${job.result}` : "",
      job.error ? `\n\nError: ${job.error}` : "",
    ].join("");
    row.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      detail.hidden = !detail.hidden;
    });
    if (job.error) {
      const errLine = document.createElement("div");
      errLine.className = "ach-desc";
      errLine.style.color = "var(--accent-bad)";
      errLine.textContent = `error: ${job.error}`;
      row.appendChild(errLine);
    }
    row.appendChild(title);
    row.appendChild(desc);
    row.appendChild(detail);
    const actions = document.createElement("div");
    actions.className = "row-actions";
    if (job.status === "pending" || job.status === "claimed") {
      const cancelBtn = document.createElement("button");
      cancelBtn.className = "btn";
      cancelBtn.type = "button";
      cancelBtn.textContent = "✕ Cancel";
      cancelBtn.addEventListener("click", () => agentCancelJob(job.id));
      actions.appendChild(cancelBtn);
    }
    if (job.status === "failed" || job.status === "cancelled") {
      const retryBtn = document.createElement("button");
      retryBtn.className = "btn";
      retryBtn.type = "button";
      retryBtn.textContent = "↻ Retry";
      retryBtn.addEventListener("click", () => agentRetryJob(job.id));
      actions.appendChild(retryBtn);
    }
    if (actions.childElementCount > 0) row.appendChild(actions);
    root.appendChild(row);
  }
}

async function agentCancelJob(id) {
  try {
    await api(API.agentCancel(id), { method: "POST" });
    showToast(`Job ${id.slice(0, 8)} cancelled`);
    await loadAgentJobs();
  } catch (e) { showToast(`Cancel failed: ${e.message}`, "error"); }
}

async function agentRetryJob(id) {
  try {
    const res = await api(API.agentRetry(id), { method: "POST" });
    showToast(`Re-enqueued as ${res.job_id.slice(0, 8)}`);
    await loadAgentJobs();
  } catch (e) { showToast(`Retry failed: ${e.message}`, "error"); }
}

async function submitAgentJob(ev) {
  ev.preventDefault();
  const prompt = $("#agentPrompt").value.trim();
  if (!prompt) return showToast("Prompt cannot be blank", "warn");
  const kind = ($("#agentKind").value.trim() || "oneshot");
  const priority = Number($("#agentPriority").value || 0);
  try {
    const res = await api(API.agentRun, {
      method: "POST",
      body: JSON.stringify({ prompt, kind, priority }),
    });
    showToast(`Job ${res.job_id.slice(0, 8)} enqueued`);
    $("#agentPrompt").value = "";
    await loadAgentJobs();
    await loadAgentHealth();
  } catch (e) { showToast(`Run failed: ${e.message}`, "error"); }
}

// ---------------------------------------------------------------------------
// P1-15: Battery widget — polls /api/hardware/pisugar, hides on 404/503
// ---------------------------------------------------------------------------

async function loadBattery() {
  const widget = $("#batteryWidget");
  const banner = $("#lowBatteryBanner");
  if (!widget) return;
  try {
    const data = await api(API.hardwarePisugar);
    const pct = Math.round(data.battery_percent ?? 0);
    const charging = data.is_charging ? " ⚡" : "";
    widget.textContent = `🔋 ${pct}%${charging}`;
    widget.hidden = false;
    // HW-12: show low battery banner when < 15% and not charging
    if (banner) banner.hidden = data.is_charging || pct >= 15;
  } catch (_) {
    // 404 or 503 means PiSugar is not present — hide widget silently
    widget.hidden = true;
    if (banner) banner.hidden = true;
  }
}

function bindEvents() {
  $("#menuBtn").addEventListener("click", () => openDrawer($("#drawer")));
  $("#settingsBtn").addEventListener("click", () => openDrawer($("#settingsDrawer")));
  $("#navSettingsBtn").addEventListener("click", () => openDrawer($("#settingsDrawer")));
  $("#navSearchBtn").addEventListener("click", () => $("#searchInput").focus());
  $("#scrim").addEventListener("click", closeDrawers);

  // P0-3: keyboard handling — Escape closes drawers, Tab traps focus
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeDrawers();
      return;
    }
    if (e.key === "Tab") {
      const openDrw = $$(".drawer.is-open")[0];
      if (!openDrw) return;
      const focusable = getFocusableElements(openDrw);
      if (focusable.length < 2) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
  });

  $("#searchInput").addEventListener("input", (e) => {
    const q = e.target.value.trim();
    if (q.length >= 2) runSearch(q);
    else if (q === "") loadDumps();
  });

  $("#dumpFilterInput")?.addEventListener("input", (e) => {
    state.dumpFilter = e.target.value;
    renderDumps();
  });
  $("#dumpStatusFilter")?.addEventListener("change", (e) => {
    state.dumpStatusFilter = e.target.value;
    renderDumps();
  });
  $("#dumpSortSelect")?.addEventListener("change", (e) => {
    state.dumpSort = e.target.value;
    renderDumps();
  });
  $("#themeToggle")?.addEventListener("change", (e) => applyTheme(e.target.value));

  $("#newDumpForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = $("#newTitle").value.trim();
    const audio_path = $("#newAudio").value.trim() || null;
    if (!title) return;
    try {
      const dump = await api(API.dumps, { method: "POST", body: JSON.stringify({ title, audio_path }) });
      setMascotState("listening");
      await loadDumps();
      selectDump(dump.id);
      $("#newDumpForm").reset();
      showToast(`Created dump #${dump.id}`);
    } catch (err) { showToast(`Create failed: ${err.message}`, "error"); }
  });

  $("#providerForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#provName").value.trim();
    const kind = $("#provKind").value;
    const enabled = $("#provEnabled").checked;
    let config = {};
    const raw = $("#provConfig").value.trim();
    if (raw) {
      try { config = JSON.parse(raw); }
      catch { return showToast("Config must be valid JSON", "warn"); }
    }
    try {
      await api(API.providers, { method: "POST", body: JSON.stringify({ name, kind, enabled, config }) });
      $("#providerForm").reset();
      showToast(`Provider ${name} saved`);
    } catch (err) { showToast(`Save failed: ${err.message}`, "error"); }
  });

  $("#saveProfileName")?.addEventListener("click", saveProfileName);
  $("#newProfileName")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); saveProfileName(); }
  });
  $("#saveDefaultLlm")?.addEventListener("click", saveDefaultLlm);

  const recBtn = $("#recordBtn");
  recBtn.addEventListener("click", async () => {
    if (!state.selectedId) return showToast("Select or create a dump first", "warn");
    state.recording = !state.recording;
    recBtn.classList.toggle("is-recording", state.recording);
    const status = state.recording ? "listening" : "idle";
    try {
      await api(API.status(state.selectedId), { method: "PATCH", body: JSON.stringify({ status }) });
      setMascotState(status);
    } catch (err) { showToast(`Status update failed: ${err.message}`, "error"); }
  });

  bindPttButton();
  bindStorageEvents();
  bindAgentEvents();
}

function bindAgentEvents() {
  const form = $("#agentRunForm");
  if (form) form.addEventListener("submit", submitAgentJob);
  const refresh = $("#agentRefreshBtn");
  if (refresh) refresh.addEventListener("click", () => {
    loadAgentHealth();
    loadAgentJobs();
  });
}

function bindPttButton() {
  const btn = $("#pttHoldBtn");
  if (!btn) return;
  const onStart = async (ev) => {
    ev.preventDefault();
    if (!state.selectedId) {
      showToast("Select or create a dump first", "warn");
      return;
    }
    if (state.pttJobId) return;
    btn.classList.add("is-ptt-active");
    btn.setAttribute("aria-pressed", "true");
    // UI-03: optional haptic feedback + recording timer
    if (navigator.vibrate) navigator.vibrate(30);
    startPttTimer();
    try {
      const res = await api(API.pttStart, {
        method: "POST",
        body: JSON.stringify({ dump_id: state.selectedId, duration_s: 5.0 }),
      });
      state.pttJobId = res.job_id;
      startPttPolling(res.job_id);
    } catch (err) {
      btn.classList.remove("is-ptt-active");
      btn.setAttribute("aria-pressed", "false");
      stopPttTimer();
      showToast(`🎙 PTT start failed: ${err.message}`, "error");
    }
  };
  const onEnd = async (ev) => {
    ev.preventDefault();
    stopPttTimer();
    if (!state.pttJobId) {
      btn.classList.remove("is-ptt-active");
      btn.setAttribute("aria-pressed", "false");
      return;
    }
    try {
      await api(API.pttCancel, {
        method: "POST",
        body: JSON.stringify({ job_id: state.pttJobId }),
      });
    } catch (err) {
      showToast(`🎙 PTT cancel failed: ${err.message}`, "error");
    }
  };
  btn.addEventListener("mousedown", onStart);
  btn.addEventListener("touchstart", onStart, { passive: false });
  btn.addEventListener("mouseup", onEnd);
  btn.addEventListener("mouseleave", onEnd);
  btn.addEventListener("touchend", onEnd);
  btn.addEventListener("touchcancel", onEnd);
  btn.addEventListener("click", (ev) => ev.preventDefault());
}

async function loadStorageStatus() {
  const root = $("#storageStatus");
  if (!root) return;
  try {
    const data = await api(API.storageStatus);
    renderStorageStatus(data);
  } catch (e) {
    const dot = $("#storageRcloneDot");
    const label = $("#storageRcloneLabel");
    if (dot) dot.style.background = "var(--accent-bad)";
    if (label) label.textContent = `Storage unavailable: ${e.message}`;
  }
}

function renderStorageStatus(data) {
  const dot = $("#storageRcloneDot");
  const label = $("#storageRcloneLabel");
  if (dot) dot.style.background = data.rclone_available ? "var(--accent-good)" : "var(--accent-bad)";
  if (label) {
    label.textContent = data.rclone_available
      ? `rclone ready · ${data.remote}`
      : `rclone missing · remote ${data.remote}`;
  }
  const remoteInput = $("#storageRemote");
  if (remoteInput) {
    remoteInput.value = data.remote || "";
    remoteInput.placeholder = data.remote || "gdrive:";
  }
  const layout = $("#storageHostLayout");
  if (layout) {
    const entries = Object.entries(data.host_layout || {})
      .map(([k, v]) => `${k}: ${v}`)
      .join("\n");
    layout.textContent = entries || "—";
  }
  const lastExport = $("#storageLastExport");
  if (lastExport) {
    lastExport.textContent = data.last_export_at
      ? new Date(data.last_export_at + "Z").toLocaleString()
      : "never";
  }
  const lastSync = $("#storageLastSync");
  if (lastSync) {
    const syncTime = data.last_sync_at
      ? new Date(data.last_sync_at + "Z").toLocaleString()
      : "never";
    const last = data.last_sync_result;
    const detail = last
      ? (last.ok
          ? ` · ${last.files_transferred} files · ${last.bytes_transferred} B`
          : ` · failed: ${last.error || "unknown"}`)
      : "";
    lastSync.textContent = `${syncTime}${detail}`;
  }
}

async function storageSync() {
  const btn = $("#storageSyncBtn");
  if (!btn) return;
  btn.disabled = true;
  try {
    const res = await fetch(API.storageSync, { method: "POST", headers: { "content-type": "application/json" } });
    const body = await res.json().catch(() => ({}));
    if (res.ok && body.ok) {
      showToast("☁ Sync complete");
    } else {
      showToast(`☁ Sync failed: ${body.detail || body.error || res.statusText}`, "error");
    }
    await loadStorageStatus();
  } catch (e) {
    showToast(`☁ Sync error: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

async function storageExport() {
  const btn = $("#storageExportBtn");
  if (!btn) return;
  btn.disabled = true;
  try {
    const res = await api(API.storageExport, { method: "POST", body: JSON.stringify({}) });
    showToast(`📦 Exported bundle (${res.size_bytes} B)`);
    await loadStorageStatus();
  } catch (e) {
    showToast(`📦 Export failed: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

async function storageImport() {
  const fileInput = $("#storageImportFile");
  const btn = $("#storageImportBtn");
  if (!fileInput || !btn) return;
  const file = fileInput.files && fileInput.files[0];
  if (!file) return showToast("Pick a .zip bundle first", "warn");
  btn.disabled = true;
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(API.storageImport, { method: "POST", body: fd });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${text || res.statusText}`);
    }
    const body = await res.json();
    showToast(`📥 Imported ${body.manifest.dump_count} dumps`);
    await loadStorageStatus();
    await loadDumps();
  } catch (e) {
    showToast(`📥 Import failed: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

async function storageRedactPreview() {
  const input = $("#storageRedactInput");
  const output = $("#storageRedactOutput");
  const btn = $("#storageRedactBtn");
  if (!input || !output || !btn) return;
  const raw = input.value.trim();
  if (!raw) return showToast("Paste a config JSON first", "warn");
  let parsed;
  try { parsed = JSON.parse(raw); }
  catch { return showToast("Config must be valid JSON", "warn"); }
  btn.disabled = true;
  try {
    const res = await api(API.storageRedact, {
      method: "POST",
      body: JSON.stringify({ config: parsed }),
    });
    output.textContent = JSON.stringify(res.redacted, null, 2);
  } catch (e) {
    showToast(`Redact preview failed: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

function bindStorageEvents() {
  const syncBtn = $("#storageSyncBtn");
  const exportBtn = $("#storageExportBtn");
  const importBtn = $("#storageImportBtn");
  const redactBtn = $("#storageRedactBtn");
  const remoteInput = $("#storageRemote");
  syncBtn?.addEventListener("click", storageSync);
  exportBtn?.addEventListener("click", storageExport);
  importBtn?.addEventListener("click", storageImport);
  redactBtn?.addEventListener("click", storageRedactPreview);
  const saved = localStorage.getItem("vibedump.storageRemote");
  if (saved && remoteInput && !remoteInput.value) remoteInput.value = saved;
  remoteInput?.addEventListener("change", (e) => {
    localStorage.setItem("vibedump.storageRemote", e.target.value.trim());
    showToast("Remote stored locally (UI only — set VIBEDUMP_RCLONE_REMOTE for the server)");
  });
}

// UI-14: First-run onboarding overlay
function initOnboarding() {
  if (localStorage.getItem("vibedump.onboarded")) return;
  const overlay = $("#onboardingOverlay");
  if (!overlay) return;
  overlay.hidden = false;

  let step = 0;
  const steps = $$(".onboarding-step");
  const dots = $$(".onboarding-dot");
  const prevBtn = $("#onboardingPrev");
  const nextBtn = $("#onboardingNext");
  const doneBtn = $("#onboardingDone");

  function goTo(n) {
    if (steps[step]) steps[step].hidden = true;
    if (dots[step]) dots[step].classList.remove("is-active");
    step = n;
    if (steps[step]) steps[step].hidden = false;
    if (dots[step]) dots[step].classList.add("is-active");
    if (prevBtn) prevBtn.hidden = step === 0;
    if (nextBtn) nextBtn.hidden = step === steps.length - 1;
    if (doneBtn) doneBtn.hidden = step !== steps.length - 1;
  }

  if (prevBtn) prevBtn.addEventListener("click", () => { if (step > 0) goTo(step - 1); });
  if (nextBtn) nextBtn.addEventListener("click", () => { if (step < steps.length - 1) goTo(step + 1); });
  if (doneBtn) doneBtn.addEventListener("click", () => {
    localStorage.setItem("vibedump.onboarded", "1");
    overlay.hidden = true;
  });

  goTo(0);
}

// UI-08: Bottom tab bar navigation
function bindTabBar() {
  const tabBtns = $$(".tab-btn");
  const pttBtn = $("#pttHoldBtn");
  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      if (tab === "record") {
        // Record tab: delegate to PTT button (focus / trigger area)
        if (pttBtn) pttBtn.focus();
        return;
      }
      tabBtns.forEach((b) => { if (b.dataset.tab !== "record") b.classList.remove("is-active"); });
      btn.classList.add("is-active");
      if (tab === "dumps") {
        const dumpsView = $("#dumpsView");
        if (dumpsView) dumpsView.scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (tab === "search") {
        const searchInput = $("#searchInput");
        if (searchInput) { searchInput.focus(); searchInput.scrollIntoView({ behavior: "smooth", block: "center" }); }
      } else if (tab === "settings") {
        openDrawer($("#settingsDrawer"));
      }
    });
  });
}

async function init() {
  bindEvents();
  bindTabBar();
  initOnboarding();
  initTheme();
  try {
    const idleImg = $("#mascotImg");
    if (idleImg) idleImg.src = API.mascot("idle");
    state.mascotState = "idle";
    await loadDumps();
    await Promise.all([
      loadProviders(),
      loadProfile(),
      loadAchievements(),
      loadDefaultLlmOptions(),
      loadStorageStatus(),
      loadAgentHealth(),
      loadAgentJobs(),
      loadBattery(),
    ]);
    setMascotState("idle");
    connectSSE();
    // Refresh battery every 60 s
    setInterval(loadBattery, 60_000);
  } catch (e) {
    showToast(`Init failed: ${e.message}`, "error");
  }
}
init();
