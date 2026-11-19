"use strict";
/* TaskFlow single-page UI. No framework and no build step. All user text is inserted with textContent
   (via h()), never innerHTML, so task titles cannot inject markup. */

const STATUSES = [["todo", "To do"], ["in_progress", "In progress"], ["review", "Review"], ["done", "Done"]];
const PRIORITIES = ["low", "medium", "high", "urgent"];
const STATUS_LABEL = Object.fromEntries(STATUSES);
const state = { token: sessionStorage.getItem("token"), user: null, projects: [], project: null, members: [], tab: "board",
                filters: { status: "", priority: "", assignee: "", q: "", overdue: false, sort: "-id" }, page: 1, live: false };
let stream = null, refreshTimer = null, streamRetry = null;

// ---------- tiny helpers ----------
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const c of children.flat()) { if (c == null || c === false) continue; el.append(c instanceof Node ? c : document.createTextNode(String(c))); }
  return el;
}
const today = () => new Date().toISOString().slice(0, 10);
const canEdit = () => state.project && ["member", "manager", "owner"].includes(state.project.my_role);
const canManage = () => state.project && ["manager", "owner"].includes(state.project.my_role);
const isOwner = () => state.project && state.project.my_role === "owner";

function toast(text) {
  const t = h("div", { class: "toast" }, text);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4000);
}
function $(sel, root) { return (root || document).querySelector(sel); }

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: Object.assign({ "Content-Type": "application/json" }, state.token ? { Authorization: "Bearer " + state.token } : {}),
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && state.token) { logout(); throw new Error("Session expired, please sign in again"); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data.error && data.error.message) || res.statusText);
  return data;
}
const qs = (obj) => { const p = new URLSearchParams(); for (const [k, v] of Object.entries(obj)) if (v !== "" && v !== false && v != null) p.set(k, v === true ? "1" : v); return p.toString(); };

// ---------- auth ----------
function logout() {
  state.token = null; state.user = null; state.project = null; state.projects = [];
  sessionStorage.removeItem("token");
  disconnectStream();
  render();
}
function authView() {
  let mode = "login";
  const err = h("div", { class: "error" });
  const name = h("input", { placeholder: "Your name", autocomplete: "name" });
  const email = h("input", { type: "email", placeholder: "you@example.com", autocomplete: "email" });
  const pass = h("input", { type: "password", placeholder: "At least 8 characters", autocomplete: "current-password" });
  const nameRow = h("div", {}, h("label", {}, "Name"), name);
  const title = h("p", {}, "Sign in to continue");
  const submit = h("button", { class: "primary", onclick: go }, "Sign in");
  const toggle = h("button", { onclick: switchMode }, "Create account");
  function switchMode() {
    mode = mode === "login" ? "register" : "login";
    nameRow.style.display = mode === "register" ? "" : "none";
    submit.textContent = mode === "login" ? "Sign in" : "Create account";
    toggle.textContent = mode === "login" ? "Create account" : "I have an account";
    title.textContent = mode === "login" ? "Sign in to continue" : "Create your account";
    err.textContent = "";
  }
  async function go() {
    err.textContent = ""; submit.disabled = true;
    try {
      const body = mode === "login" ? { email: email.value, password: pass.value } : { name: name.value, email: email.value, password: pass.value };
      const data = await api("/auth/" + mode, { method: "POST", body });
      state.token = data.token; sessionStorage.setItem("token", data.token); state.user = data.user;
      await startApp();
    } catch (e) { err.textContent = e.message; } finally { submit.disabled = false; }
  }
  nameRow.style.display = "none";
  pass.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  return h("div", { class: "auth" }, h("h1", {}, "TaskFlow"), title, nameRow,
    h("label", {}, "Email"), email, h("label", {}, "Password"), pass, err, h("div", { class: "row" }, submit, toggle));
}

// ---------- app shell ----------
async function startApp() {
  try {
    state.user = await api("/auth/me");
    await loadProjects();
    render();
    connectStream();
  } catch (e) { toast(e.message); }
}
async function loadProjects() {
  const data = await api("/projects?page_size=100");
  state.projects = data.items;
  if (state.project) state.project = state.projects.find((p) => p.id === state.project.id) || null;
}
function render() {
  const root = $("#app");
  root.replaceChildren();
  if (!state.token) { root.append(authView()); return; }
  root.append(
    h("header", { class: "top" }, h("span", { class: "brand" }, "TaskFlow"), h("span", { class: "spacer" }),
      h("span", { class: "live" + (state.live ? " on" : ""), id: "live" }, state.live ? "● Live" : "○ Connecting…"),
      state.user && h("span", {}, state.user.name), state.user && state.user.role === "admin" && h("span", { class: "badge" }, "admin"),
      h("button", { onclick: logout }, "Sign out")),
    h("div", { class: "layout" }, sidebar(), h("main", { id: "main" })));
  renderMain();
}
function sidebar() {
  const list = state.projects.map((p) => h("div", { class: "item" + (state.project && state.project.id === p.id ? " active" : ""), onclick: () => selectProject(p.id) },
    h("span", {}, p.name), h("span", { class: "badge" }, p.open_tasks ?? 0)));
  return h("aside", {},
    h("div", { class: "item" + (state.project ? "" : " active"), onclick: () => selectProject(null) }, h("span", {}, "My dashboard")),
    h("h4", {}, "Projects"), ...list, !list.length && h("div", { class: "empty" }, "No projects yet"),
    h("button", { style: "margin-top:10px;width:100%", onclick: newProject }, "+ New project"));
}
async function newProject() {
  const name = prompt("Project name?");
  if (!name || !name.trim()) return;
  try { const p = await api("/projects", { method: "POST", body: { name: name.trim() } }); await loadProjects(); await selectProject(p.id); }
  catch (e) { toast(e.message); }
}
async function selectProject(id) {
  state.project = id ? state.projects.find((p) => p.id === id) : null;
  state.tab = "board"; state.page = 1;
  state.filters = { status: "", priority: "", assignee: "", q: "", overdue: false, sort: "-id" };
  state.members = state.project ? (await api(`/projects/${id}/members`)).items : [];
  render();
}
function renderMain() {
  const main = $("#main");
  if (!main) return;
  if (!state.project) return renderHome(main).catch((e) => toast(e.message));
  const p = state.project;
  const tab = (key, label) => h("button", { class: state.tab === key ? "active" : "", onclick: () => { state.tab = key; renderMain(); } }, label);
  const body = h("div", { id: "tabbody" });
  main.replaceChildren(h("h2", { style: "margin:0 0 4px" }, p.name), h("div", { class: "empty", style: "padding:0 0 12px" }, p.description || "", " ",
    h("span", { class: "badge" }, "you: " + p.my_role)),
    h("div", { class: "tabs" }, tab("board", "Board"), tab("tasks", "Tasks"), tab("dashboard", "Dashboard"), tab("members", "Members")), body);
  ({ board: renderBoard, tasks: renderTasks, dashboard: renderDashboard, members: renderMembers })[state.tab](body).catch((e) => toast(e.message));
}
// Called by live events: re-render the visible tab without losing the user's place in the UI.
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    try { await loadProjects(); const side = $("aside"); if (side) side.replaceWith(sidebar());
      if (state.project) state.members = (await api(`/projects/${state.project.id}/members`)).items;
      if (!$("dialog[open]")) renderMain(); } catch (e) { /* ignore transient errors */ }
  }, 300);
}

// ---------- home ----------
async function renderHome(main) {
  const d = await api("/dashboard");
  main.replaceChildren(h("h2", { style: "margin-top:0" }, "My dashboard"),
    h("div", { class: "stats" }, stat(d.projects, "Projects"), stat(d.overdue, "My overdue tasks"),
      ...STATUSES.map(([k, l]) => stat(d.assigned_by_status[k], "Assigned: " + l))),
    h("div", { class: "panel" }, h("h3", {}, "Due in the next 7 days"),
      d.due_soon.length ? d.due_soon.map((t) => h("div", { style: "padding:4px 0" }, t.due_date + "  ·  ", t.title, "  ", h("span", { class: "badge" }, t.project_name))) : h("div", { class: "empty" }, "Nothing due soon")));
}
const stat = (n, l) => h("div", { class: "stat" }, h("div", { class: "n" }, n), h("div", { class: "l" }, l));

// ---------- board ----------
async function renderBoard(body) {
  const pid = state.project.id;
  const results = await Promise.all(STATUSES.map(([s]) => api(`/projects/${pid}/tasks?` + qs({ status: s, page_size: 50, sort: "-updated_at" }))));
  body.replaceChildren(
    h("div", { class: "toolbar" }, canEdit() && h("button", { class: "primary", onclick: () => taskDialog(null) }, "+ New task")),
    h("div", { class: "board" }, STATUSES.map(([s, label], i) => h("div", { class: "col" },
      h("h3", {}, h("span", {}, label), h("span", {}, results[i].total)),
      results[i].items.map((t) => taskCard(t, i)),
      results[i].total > results[i].items.length && h("div", { class: "empty" }, `+ ${results[i].total - results[i].items.length} more (see Tasks tab)`)))));
}
function taskCard(t, col) {
  const late = t.due_date && t.status !== "done" && t.due_date < today();
  const move = async (delta) => {
    try { await api("/tasks/" + t.id, { method: "PATCH", body: { status: STATUSES[col + delta][0] } }); } catch (e) { toast(e.message); }
  };
  return h("div", { class: "card" }, h("div", { class: "title", onclick: () => taskDialog(t) }, t.title),
    h("div", { class: "meta" }, h("span", { class: "badge p-" + t.priority }, t.priority), t.assignee_name && h("span", {}, "👤 " + t.assignee_name),
      t.due_date && h("span", { class: late ? "late" : "" }, "📅 " + t.due_date + (late ? " (overdue)" : ""))),
    (canEdit() ? h("div", { class: "moves" }, col > 0 && h("button", { title: "Move left", onclick: () => move(-1) }, "←"),
      col < 3 && h("button", { title: "Move right", onclick: () => move(1) }, "→")) : null));
}

// ---------- task dialog ----------
function taskDialog(task) {
  const isNew = !task;
  const f = {
    title: h("input", { value: task ? task.title : "", maxlength: 200 }),
    description: h("textarea", { rows: 3 }),
    status: h("select", {}, STATUSES.map(([k, l]) => h("option", { value: k }, l))),
    priority: h("select", {}, PRIORITIES.map((p) => h("option", { value: p }, p))),
    assignee: h("select", {}, h("option", { value: "" }, "Unassigned"), state.members.map((m) => h("option", { value: m.id }, m.name))),
    due: h("input", { type: "date" }),
  };
  f.description.value = task ? task.description : "";
  f.status.value = task ? task.status : "todo";
  f.priority.value = task ? task.priority : "medium";
  f.assignee.value = task && task.assignee_id ? String(task.assignee_id) : "";
  f.due.value = task && task.due_date ? task.due_date : "";
  const err = h("div", { class: "error" });
  const dlg = h("dialog", {});
  const close = () => { dlg.close(); dlg.remove(); };
  const save = async () => {
    err.textContent = "";
    const body = { title: f.title.value, description: f.description.value, status: f.status.value, priority: f.priority.value,
                   assignee_id: f.assignee.value ? Number(f.assignee.value) : null, due_date: f.due.value || null };
    try {
      if (isNew) await api(`/projects/${state.project.id}/tasks`, { method: "POST", body });
      else await api("/tasks/" + task.id, { method: "PATCH", body });
      close(); scheduleRefresh();
    } catch (e) { err.textContent = e.message; }
  };
  const del = async () => {
    if (!confirm("Delete this task?")) return;
    try { await api("/tasks/" + task.id, { method: "DELETE" }); close(); scheduleRefresh(); } catch (e) { err.textContent = e.message; }
  };
  dlg.append(h("h3", { style: "margin-top:0" }, isNew ? "New task" : "Edit task"),
    h("label", {}, "Title"), f.title, h("label", {}, "Description"), f.description,
    h("label", {}, "Status"), f.status, h("label", {}, "Priority"), f.priority,
    h("label", {}, "Assignee"), f.assignee, h("label", {}, "Due date"), f.due, err,
    h("div", { class: "actions" }, !isNew && canManage() && h("button", { class: "danger", onclick: del }, "Delete"), h("span", { class: "spacer" }),
      h("button", { onclick: close }, "Cancel"), canEdit() && h("button", { class: "primary", onclick: save }, "Save")));
  dlg.addEventListener("cancel", () => dlg.remove());
  document.body.append(dlg); dlg.showModal();
}

// ---------- task list (paginated + filters) ----------
async function renderTasks(body) {
  const pid = state.project.id, F = state.filters, size = 10;
  const data = await api(`/projects/${pid}/tasks?` + qs({ status: F.status, priority: F.priority, assignee: F.assignee, q: F.q,
    overdue: F.overdue, sort: F.sort, page: state.page, page_size: size }));
  const sel = (key, options) => { const s = h("select", { onchange: (e) => { F[key] = e.target.value; state.page = 1; renderMain(); } }, options.map(([v, l]) => h("option", { value: v }, l))); s.value = F[key]; return s; };
  let timer; const search = h("input", { placeholder: "Search…", value: F.q, style: "min-width:160px", oninput: (e) => { clearTimeout(timer); timer = setTimeout(() => { F.q = e.target.value; state.page = 1; renderMain(); }, 350); } });
  body.replaceChildren(
    h("div", { class: "toolbar" }, search,
      sel("status", [["", "All statuses"], ...STATUSES]), sel("priority", [["", "All priorities"], ...PRIORITIES.map((p) => [p, p])]),
      sel("assignee", [["", "Anyone"], ["me", "Assigned to me"], ["none", "Unassigned"]]),
      sel("sort", [["-id", "Newest"], ["id", "Oldest"], ["due_date", "Due soonest"], ["-updated_at", "Recently updated"], ["title", "Title A–Z"]]),
      h("label", { style: "margin:0;display:flex;gap:4px;align-items:center" }, h("input", { type: "checkbox", style: "width:auto", checked: F.overdue, onchange: (e) => { F.overdue = e.target.checked; state.page = 1; renderMain(); } }), "Overdue"),
      h("span", { class: "spacer" }), canEdit() && h("button", { class: "primary", onclick: () => taskDialog(null) }, "+ New task")),
    data.items.length ? h("table", {}, h("thead", {}, h("tr", {}, ["Title", "Status", "Priority", "Assignee", "Due"].map((c) => h("th", {}, c)))),
      h("tbody", {}, data.items.map((t) => h("tr", { class: "click", onclick: () => taskDialog(t) },
        h("td", {}, t.title), h("td", {}, STATUS_LABEL[t.status]), h("td", {}, h("span", { class: "badge p-" + t.priority }, t.priority)),
        h("td", {}, t.assignee_name || "—"), h("td", { class: t.due_date && t.status !== "done" && t.due_date < today() ? "late" : "" }, t.due_date || "—")))))
      : h("div", { class: "empty" }, "No tasks match these filters."),
    h("div", { class: "pager" }, h("span", {}, `${data.total} task${data.total === 1 ? "" : "s"} · page ${data.total_pages ? data.page : 0} of ${data.total_pages}`),
      h("button", { disabled: state.page <= 1, onclick: () => { state.page--; renderMain(); } }, "‹ Prev"),
      h("button", { disabled: state.page >= data.total_pages, onclick: () => { state.page++; renderMain(); } }, "Next ›")));
  if (document.activeElement === document.body && F.q) { const s = $("input[placeholder='Search…']"); if (s) { s.focus(); s.setSelectionRange(F.q.length, F.q.length); } }
}

// ---------- dashboard ----------
const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs) { const el = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v); return el; }
function hbars(rows, color) {          // rows: [[label, value]]
  const max = Math.max(1, ...rows.map((r) => r[1])), W = 300, rowH = 26;
  const el = svg("svg", { viewBox: `0 0 ${W} ${rows.length * rowH}`, width: "100%", role: "img" });
  rows.forEach(([label, value], i) => {
    const y = i * rowH, w = (value / max) * 150;
    const text = svg("text", { x: 0, y: y + 17, "font-size": 12, fill: "#6b7488" }); text.textContent = label; el.append(text);
    el.append(svg("rect", { x: 100, y: y + 5, width: Math.max(w, 1), height: 14, rx: 3, fill: color }));
    const num = svg("text", { x: 106 + w, y: y + 17, "font-size": 12, fill: "#1d2433" }); num.textContent = value; el.append(num);
  });
  return el;
}
function vbars(rows, color) {          // rows: [[label, value]]
  const max = Math.max(1, ...rows.map((r) => r[1])), W = 300, H = 130, bw = W / rows.length;
  const el = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img" });
  rows.forEach(([label, value], i) => {
    const bh = (value / max) * 80, x = i * bw + 6;
    el.append(svg("rect", { x, y: 95 - bh, width: bw - 12, height: Math.max(bh, 1), rx: 3, fill: color }));
    const n = svg("text", { x: x + (bw - 12) / 2, y: 90 - bh, "font-size": 11, "text-anchor": "middle", fill: "#1d2433" }); n.textContent = value; el.append(n);
    const l = svg("text", { x: x + (bw - 12) / 2, y: 112, "font-size": 10, "text-anchor": "middle", fill: "#6b7488" }); l.textContent = label; el.append(l);
  });
  return el;
}
async function renderDashboard(body) {
  const d = await api(`/projects/${state.project.id}/dashboard`);
  body.replaceChildren(
    h("div", { class: "stats" }, stat(d.total, "Total tasks"), stat(d.open, "Open"), stat(d.done, "Done"), stat(d.overdue, "Overdue"), stat(d.unassigned, "Unassigned (open)")),
    h("div", { class: "panels" },
      h("div", { class: "panel" }, h("h3", {}, "By status"), hbars(STATUSES.map(([k, l]) => [l, d.by_status[k]]), "#3b5bdb")),
      h("div", { class: "panel" }, h("h3", {}, "Open tasks by priority"), hbars(PRIORITIES.map((p) => [p, d.by_priority[p]]), "#f08c00")),
      h("div", { class: "panel" }, h("h3", {}, "Completed in the last 7 days"), vbars(d.completed_last_7_days.map((x) => [x.date.slice(5), x.count]), "#2f9e44")),
      h("div", { class: "panel" }, h("h3", {}, "Workload (open tasks)"), d.workload.length ? hbars(d.workload.map((w) => [w.name.slice(0, 14), w.open_tasks]), "#7048e8") : h("div", { class: "empty" }, "No assigned open tasks"))));
}

// ---------- members ----------
async function renderMembers(body) {
  const pid = state.project.id;
  const data = await api(`/projects/${pid}/members`);
  state.members = data.items;
  const email = h("input", { type: "email", placeholder: "teammate@example.com" });
  const role = h("select", {}, ["viewer", "member", "manager"].map((r) => h("option", { value: r }, r))); role.value = "member";
  const err = h("div", { class: "error" });
  body.replaceChildren(
    h("table", {}, h("thead", {}, h("tr", {}, ["Name", "Email", "Role", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, data.items.map((m) => {
        const locked = m.role === "owner" || !isOwner();
        const sel = h("select", { disabled: locked, onchange: async (e) => { try { await api(`/projects/${pid}/members/${m.id}`, { method: "PUT", body: { role: e.target.value } }); } catch (x) { toast(x.message); } } },
          (m.role === "owner" ? ["owner"] : ["viewer", "member", "manager"]).map((r) => h("option", { value: r }, r))); sel.value = m.role;
        return h("tr", {}, h("td", {}, m.name), h("td", {}, m.email), h("td", {}, sel),
          h("td", {}, !locked && h("button", { class: "danger", onclick: async () => { if (confirm("Remove " + m.name + "?")) try { await api(`/projects/${pid}/members/${m.id}`, { method: "DELETE" }); } catch (x) { toast(x.message); } } }, "Remove")));
      }))),
    isOwner() && h("div", { class: "panel", style: "margin-top:14px" }, h("h3", {}, "Add a member (they must already have an account)"),
      h("div", { class: "toolbar" }, email, role, h("button", { class: "primary", onclick: async () => {
        err.textContent = ""; try { await api(`/projects/${pid}/members`, { method: "POST", body: { email: email.value, role: role.value } }); email.value = ""; }
        catch (x) { err.textContent = x.message; } } }, "Add")), err));
}

// ---------- live updates (Server-Sent Events) ----------
async function connectStream() {
  disconnectStream();
  try {
    const { token } = await api("/auth/stream-token", { method: "POST" });
    stream = new EventSource("/api/stream?token=" + encodeURIComponent(token));
  } catch (e) { return scheduleStreamRetry(); }
  stream.onopen = () => setLive(true);
  stream.onerror = () => {
    setLive(false);
    // 401 (expired stream token) closes the connection for good: fetch a fresh token and reconnect.
    if (stream && stream.readyState === EventSource.CLOSED) scheduleStreamRetry();
  };
  stream.addEventListener("resync", () => scheduleRefresh());
  ["task.created", "task.updated", "task.deleted", "member.added", "member.removed", "member.role_changed", "project.created", "project.updated", "project.deleted"]
    .forEach((type) => stream.addEventListener(type, (e) => onEvent(JSON.parse(e.data))));
}
function scheduleStreamRetry() { clearTimeout(streamRetry); if (state.token) streamRetry = setTimeout(connectStream, 2500); }
function disconnectStream() { clearTimeout(streamRetry); if (stream) { stream.close(); stream = null; } setLive(false); }
function setLive(on) {
  state.live = on;
  const el = $("#live"); if (el) { el.textContent = on ? "● Live" : "○ Reconnecting…"; el.className = "live" + (on ? " on" : ""); }
}
function onEvent(ev) {
  const p = ev.payload || {}, mine = p.actor_id === (state.user && state.user.id), who = p.actor_name || "Someone";
  if (!mine) {
    const text = { "task.created": `${who} created “${p.title}”`, "task.deleted": `${who} deleted “${p.title}”`,
      "task.updated": p.previous_status && p.previous_status !== p.status ? `${who} moved “${p.title}” to ${STATUS_LABEL[p.status]}` : `${who} updated “${p.title}”`,
      "member.added": "A member joined a project", "member.removed": "A member was removed from a project", "member.role_changed": "A member's role changed",
      "project.deleted": "A project was deleted", "project.updated": "A project was renamed" }[ev.type];
    if (text) toast(text);
  }
  if (ev.type === "project.deleted" && state.project && state.project.id === ev.project_id) state.project = null;
  scheduleRefresh();
}

// ---------- boot ----------
if (state.token) startApp(); else render();
