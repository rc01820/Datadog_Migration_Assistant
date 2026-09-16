/* Datadog Migration Assistant — single-page UI (no build step). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const sidebar = $("#sidebar");
const state = { meta: null, project: null, scans: [], poll: null };

const h = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n ?? 0).toLocaleString();
const when = (iso) => (iso ? new Date(iso).toLocaleString() : "");

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${res.status})`);
  return data;
}

function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.className = "toast"), 3800);
}

function crumbs(parts) {
  $("#crumbs").innerHTML = parts.map((p) => (p.href ? `<a href="${p.href}">${h(p.label)}</a>` : `<span>${h(p.label)}</span>`)).join("<span>/</span>");
}

function stopPoll() { clearTimeout(state.poll); state.poll = null; }

/* ------------------------------------------------------------------ router */
async function route() {
  stopPoll();
  sidebar.classList.remove("open");
  if (!state.meta) state.meta = await api("/api/meta");
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  try {
    if (parts[0] === "new") return await viewConnect(parts[1] || "solarwinds");
    if (parts[0] === "p" && parts[1]) {
      await loadProject(Number(parts[1]));
      const view = parts[2] || "overview";
      renderSidebar(view, parts[3]);
      if (view === "inventory") return await viewInventory(parts[3] || "nodes");
      if (view === "rules") return viewRules();
      if (view === "reports") return await viewReports(parts[3]);
      if (view === "history") return await viewHistory();
      if (view === "settings") return await viewConnect("solarwinds", state.project);
      return await viewOverview();
    }
    return await viewHome();
  } catch (e) {
    main.innerHTML = `<div class="page empty"><h2>Something went wrong</h2><p>${h(e.message)}</p><a class="btn" href="#/">Back to migrations</a></div>`;
  } finally {
    main.focus({ preventScroll: true });
  }
}
window.addEventListener("hashchange", route);
$("#menuBtn").addEventListener("click", () => sidebar.classList.toggle("open"));

async function loadProject(id) {
  if (!state.project || state.project.id !== id) state.project = await api(`/api/projects/${id}`);
  state.scans = await api(`/api/projects/${id}/scans`);
}
const latestScan = () => state.scans.find((s) => s.status === "complete");
const runningScan = () => state.scans.find((s) => s.status === "running");

function renderSidebar(view, sub) {
  const p = state.project;
  const cats = latestScan()?.summary?.categories || {};
  const link = (href, label, active, count = "") =>
    `<a href="${href}" class="${active ? "active" : ""}"><span>${h(label)}</span><span class="count">${count}</span></a>`;
  sidebar.hidden = false;
  $("#menuBtn").hidden = false;
  sidebar.innerHTML = `
    ${link(`#/p/${p.id}`, "Overview", view === "overview")}
    <div class="group">Inventory</div>
    ${Object.entries(state.meta.categories).map(([k, m]) =>
      link(`#/p/${p.id}/inventory/${k}`, m.label, view === "inventory" && sub === k, cats[k] ? fmt(cats[k].total) : "")).join("")}
    <div class="group">Migration</div>
    ${link(`#/p/${p.id}/rules`, "Mapping rules", view === "rules")}
    ${link(`#/p/${p.id}/reports`, "Reports", view === "reports")}
    ${link(`#/p/${p.id}/history`, "Scan history", view === "history", state.scans.length || "")}
    ${link(`#/p/${p.id}/settings`, "Connections", view === "settings")}`;
  crumbs([{ label: "Migrations", href: "#/" }, { label: p.name }]);
}

function hideSidebar() { sidebar.hidden = true; $("#menuBtn").hidden = true; }

/* -------------------------------------------------------------------- home */
async function viewHome() {
  hideSidebar();
  crumbs([{ label: "Migrations" }]);
  state.project = null;
  const projects = await api("/api/projects");
  main.innerHTML = `
  <div class="page">
    <section class="hero">
      <h1>Move your monitoring to Datadog</h1>
      <p>Choose the platform you are migrating from. The assistant inventories it, maps each object to its Datadog equivalent, and tracks what has already moved every time you scan.</p>
    </section>
    <div class="tools">
      ${state.meta.tools.map((t) => `
        <button class="tool ${t.available ? "available" : ""}" data-tool="${t.id}" ${t.available ? "" : "disabled"}>
          <b>${h(t.name)}</b><span>${h(t.detail)}</span>
        </button>`).join("")}
    </div>
    <section class="projects">
      <h2>Migrations in progress</h2>
      <div class="panel" style="margin-top:12px">
        ${projects.length ? projects.map(projectRow).join("") :
          `<div class="empty"><p>No migrations yet. Pick SolarWinds above to connect your first source and Datadog org.</p></div>`}
      </div>
    </section>
  </div>`;
  main.querySelectorAll(".tool[data-tool]").forEach((b) =>
    b.addEventListener("click", () => (location.hash = `#/new/${b.dataset.tool}`)));
}

function projectRow(p) {
  const s = p.last_scan?.summary?.overall;
  return `<a class="proj-row" href="#/p/${p.id}">
    <div><b>${h(p.name)}</b><div class="small muted">SolarWinds ${h(p.source_host)}</div></div>
    <div class="small muted hide-sm">Datadog ${h(p.datadog_site)}<br>${s ? `Last scan ${when(p.last_scan.finished_at)}` : "Not scanned yet"}</div>
    <div class="hide-sm"><div class="meter"><i style="width:${s ? s.readiness : 0}%"></i></div></div>
    <div style="text-align:right"><b>${s ? s.readiness + "%" : "—"}</b><div class="small muted">migrated</div></div>
  </a>`;
}

/* ------------------------------------------------------------- connections */
async function viewConnect(tool, project = null) {
  if (!project) {
    hideSidebar();
    crumbs([{ label: "Migrations", href: "#/" }, { label: "New SolarWinds migration" }]);
  }
  if (tool !== "solarwinds") {
    main.innerHTML = `<div class="page empty"><h2>Not available yet</h2><p>This version supports SolarWinds Orion only.</p><a class="btn" href="#/">Back</a></div>`;
    return;
  }
  const src = project?.source || { port: 17774, verify_ssl: false };
  const dd = project?.datadog || { site: "datadoghq.com" };
  const sites = Object.entries(state.meta.datadog_sites)
    .map(([k, v]) => `<option value="${k}" ${dd.site === k ? "selected" : ""}>${h(v)} — ${h(k)}</option>`).join("");
  const keep = (flag) => (flag ? `placeholder="Saved. Leave blank to keep"` : "");
  main.innerHTML = `
  <div class="page stack">
    <div class="spread">
      <div><h1>${project ? "Connections" : "Connect SolarWinds and Datadog"}</h1>
      <p class="muted" style="margin-top:6px">The assistant only reads from both systems. Credentials are encrypted at rest in the app's data volume.</p></div>
    </div>
    <label class="field" style="max-width:420px">Migration name
      <input id="pname" required value="${h(project?.name || "")}" placeholder="e.g. HQ NOC SolarWinds to Datadog"></label>
    <div class="connect">
      <section class="panel">
        <div class="panel-head spread"><h2>Migrating from: SolarWinds</h2>
          <label class="check"><input type="checkbox" id="swDemo" ${src.demo ? "checked" : ""}> Demo data</label></div>
        <div class="panel-body form-grid" id="swFields">
          <label class="field">Orion server<input id="swHost" value="${h(src.host || "")}" placeholder="orion.corp.example.com"></label>
          <label class="field">SWIS port<input id="swPort" type="number" value="${h(src.port || 17774)}"></label>
          <label class="field">Username<input id="swUser" autocomplete="off" value="${h(src.username || "")}"></label>
          <label class="field">Password<input id="swPass" type="password" autocomplete="new-password" ${keep(src.password_set)}></label>
          <label class="check full"><input type="checkbox" id="swSsl" ${src.verify_ssl ? "checked" : ""}> Verify the server's TLS certificate</label>
        </div>
        <div class="panel-body row" style="border-top:1px solid var(--line)">
          <button id="swTest">Test SolarWinds</button><span class="test-result" id="swResult"></span>
        </div>
      </section>
      <div class="arrow" aria-hidden="true">→</div>
      <section class="panel">
        <div class="panel-head spread"><h2>Migrating to: Datadog</h2>
          <label class="check"><input type="checkbox" id="ddDemo" ${dd.demo ? "checked" : ""}> Demo data</label></div>
        <div class="panel-body form-grid" id="ddFields">
          <label class="field full">Datadog site<select id="ddSite">${sites}</select></label>
          <label class="field">API key<input id="ddApi" type="password" autocomplete="off" ${keep(dd.api_key_set)}></label>
          <label class="field">Application key<input id="ddApp" type="password" autocomplete="off" ${keep(dd.app_key_set)}></label>
          <p class="small muted full">The Application key needs read access to hosts, monitors, dashboards, Network Device Monitoring and the Service Catalog.</p>
        </div>
        <div class="panel-body row" style="border-top:1px solid var(--line)">
          <button id="ddTest">Test Datadog</button><span class="test-result" id="ddResult"></span>
        </div>
      </section>
    </div>
    <div class="row">
      <button class="primary" id="save">${project ? "Save connections" : "Save and run first scan"}</button>
      ${project ? `<button class="danger" id="del">Delete migration</button>` : `<a class="btn" href="#/">Cancel</a>`}
    </div>
  </div>`;

  const toggle = () => {
    $("#swFields").querySelectorAll("input").forEach((i) => (i.disabled = $("#swDemo").checked));
    $("#ddFields").querySelectorAll("input,select").forEach((i) => (i.disabled = $("#ddDemo").checked));
  };
  $("#swDemo").addEventListener("change", toggle);
  $("#ddDemo").addEventListener("change", toggle);
  toggle();

  const body = () => ({
    name: $("#pname").value.trim(), source_tool: "solarwinds", project_id: project?.id,
    source: { host: $("#swHost").value.trim(), port: Number($("#swPort").value) || 17774, username: $("#swUser").value.trim(),
      password: $("#swPass").value, verify_ssl: $("#swSsl").checked, demo: $("#swDemo").checked },
    datadog: { site: $("#ddSite").value, api_key: $("#ddApi").value.trim(), app_key: $("#ddApp").value.trim(), demo: $("#ddDemo").checked },
  });

  const test = async (target, el, btn) => {
    el.className = "test-result"; el.textContent = "Testing…"; btn.disabled = true;
    try {
      const r = await api(`/api/test/${target}`, { method: "POST", body: body() });
      if (!r.ok) throw new Error(r.error);
      el.className = "test-result ok";
      el.textContent = target === "solarwinds"
        ? `Connected to ${r.host}. ${fmt(r.nodes)} nodes${r.modules?.length ? `. ${r.modules.slice(0, 4).join(", ")}` : ""}`
        : `Connected to ${r.region}. ${fmt(r.hosts)} hosts reporting`;
    } catch (e) {
      el.className = "test-result fail"; el.textContent = e.message;
    } finally { btn.disabled = false; }
  };
  $("#swTest").addEventListener("click", (e) => test("solarwinds", $("#swResult"), e.currentTarget));
  $("#ddTest").addEventListener("click", (e) => test("datadog", $("#ddResult"), e.currentTarget));

  $("#save").addEventListener("click", async (e) => {
    const b = body();
    if (!b.name) { $("#pname").focus(); return toast("Give the migration a name first.", true); }
    if (!b.source.demo && !b.source.host) return toast("Enter the SolarWinds Orion server.", true);
    e.currentTarget.disabled = true;
    try {
      if (project) {
        state.project = await api(`/api/projects/${project.id}`, { method: "PUT", body: b });
        toast("Connections saved.");
        route();
      } else {
        const p = await api("/api/projects", { method: "POST", body: b });
        await api(`/api/projects/${p.id}/scans`, { method: "POST" });
        state.project = null;
        location.hash = `#/p/${p.id}`;
      }
    } catch (err) { toast(err.message, true); e.currentTarget.disabled = false; }
  });

  $("#del")?.addEventListener("click", async () => {
    if (!confirm(`Delete "${project.name}" and all of its scans?`)) return;
    await api(`/api/projects/${project.id}`, { method: "DELETE" });
    state.project = null;
    toast("Migration deleted.");
    location.hash = "#/";
  });
}

/* ---------------------------------------------------------------- overview */
async function startScan(btn) {
  btn.disabled = true;
  try {
    await api(`/api/projects/${state.project.id}/scans`, { method: "POST" });
    toast("Scan started.");
    route();
  } catch (e) { toast(e.message, true); btn.disabled = false; }
}

function pollRunning(then) {
  const run = runningScan();
  if (!run) return;
  state.poll = setTimeout(async () => {
    const s = await api(`/api/scans/${run.id}`).catch(() => null);
    if (!s) return;
    const bar = $("#scanBar"), msg = $("#scanMsg");
    if (bar) bar.style.width = `${s.progress}%`;
    if (msg) msg.textContent = s.message;
    if (s.status === "running") return pollRunning(then);
    toast(s.status === "complete" ? "Scan complete." : `Scan failed: ${s.message}`, s.status !== "complete");
    then();
  }, 1200);
}

function sparkline(points) {
  if (points.length < 2) return `<p class="small muted">Run more scans to see the trend.</p>`;
  const w = 260, hgt = 56, max = 100;
  const xy = points.map((p, i) => [(i / (points.length - 1)) * w, hgt - 4 - (p.readiness / max) * (hgt - 8)]);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  return `<svg class="spark" viewBox="0 0 ${w} ${hgt}" preserveAspectRatio="none" role="img" aria-label="Readiness across ${points.length} scans">
    <path class="area" d="${line}L${w},${hgt}L0,${hgt}Z"/><path class="line" d="${line}"/></svg>
    <p class="small muted">${points[0].readiness}% at scan ${points[0].scan_id}, ${points.at(-1).readiness}% now</p>`;
}

function flow(c) {
  const t = Math.max(c.total, 1);
  const pct = (n) => ((100 * n) / t).toFixed(2);
  return `<div class="flow">
    <div class="split" aria-hidden="true">
      ${[[c.auto, "auto"], [c.review, "review"], [c.unsupported, "unsup"]].filter(([n]) => n > 0)
        .map(([n, k]) => `<i style="width:${pct(n)}%;background:var(--${k})"></i>`).join("")}
    </div>
    <div class="done" title="Migrated"><i style="width:${c.in_scope ? (100 * c.migrated) / c.in_scope : 0}%"></i></div>
    <div class="legend"><span>${fmt(c.auto)} auto</span><span>${fmt(c.review)} review</span><span>${fmt(c.unsupported)} unsupported</span></div>
  </div>`;
}

async function viewOverview() {
  const p = state.project, scan = latestScan(), run = runningScan();
  const trend = scan ? await api(`/api/projects/${p.id}/trend`) : [];
  const head = `
    <div class="spread">
      <div><h1>${h(p.name)}</h1>
        <p class="muted" style="margin-top:6px">SolarWinds ${h(p.source_host)} to Datadog ${h(p.datadog_site)}${scan ? `. Last scan ${when(scan.finished_at)}` : ""}</p></div>
      <div class="row">
        ${scan ? `<a class="btn" href="/api/scans/${scan.id}/report" target="_blank" rel="noopener">Open assessment report</a>` : ""}
        <button class="primary" id="scanBtn" ${run ? "disabled" : ""}>${run ? "Scanning…" : scan ? "Run new scan" : "Run first scan"}</button>
      </div>
    </div>
    ${run ? `<div class="panel progress-card"><div class="spread"><b>Scan ${run.id} in progress</b><span class="small muted" id="scanMsg">${h(run.message)}</span></div>
      <div class="meter"><i id="scanBar" style="width:${run.progress}%"></i></div></div>` : ""}
    ${!run && state.scans[0]?.status === "failed" ? `<div class="panel warnings"><b>The last scan failed</b><ul><li>${h(state.scans[0].message)}</li></ul>
      <p class="small" style="margin-top:6px"><a href="#/p/${p.id}/settings">Check connections</a></p></div>` : ""}`;

  if (!scan) {
    main.innerHTML = `<div class="page">${head}${run ? "" : `<div class="panel empty" style="margin-top:20px"><h2>No results yet</h2>
      <p>Run a scan to inventory SolarWinds and compare it with Datadog.</p></div>`}</div>`;
    $("#scanBtn").addEventListener("click", (e) => startScan(e.currentTarget));
    return pollRunning(route);
  }

  const o = scan.summary.overall, cats = scan.summary.categories, delta = scan.delta || {};
  const newly = Object.values(delta).reduce((a, d) => a + (d.newly_migrated || 0), 0);
  main.innerHTML = `
  <div class="page">
    ${head}
    <div class="summary">
      <section class="panel readiness">
        <div class="muted">Migrated to Datadog</div>
        <div class="num">${o.readiness}<small>%</small></div>
        <div class="meter"><i style="width:${o.readiness}%"></i></div>
        <p class="small muted">${fmt(o.migrated)} of ${fmt(o.in_scope)} in-scope objects found in Datadog${scan.delta && Object.keys(scan.delta).length ? `. ${fmt(newly)} newly migrated since the previous scan` : ""}.</p>
        ${sparkline(trend)}
      </section>
      <section class="panel" style="overflow:hidden">
        <div class="kpis">
          <div class="kpi"><b>${fmt(o.total)}</b><span>SolarWinds objects</span></div>
          <div class="kpi done"><b>${fmt(o.migrated)}</b><span>Already in Datadog</span></div>
          <div class="kpi"><b>${fmt(o.pending)}</b><span>Still to migrate</span></div>
          <div class="kpi auto"><b>${fmt(o.auto)}</b><span>Automatic mapping</span></div>
          <div class="kpi review"><b>${fmt(o.review)}</b><span>Needs review</span></div>
          <div class="kpi unsupported"><b>${fmt(o.unsupported)}</b><span>Unsupported</span></div>
        </div>
        <div class="panel-body small muted" style="border-top:1px solid var(--line)">
          Datadog today: ${fmt(o.datadog?.hosts)} hosts, ${fmt(o.datadog?.ndm_devices)} network devices, ${fmt(o.datadog?.monitors)} monitors, ${fmt(o.datadog?.dashboards)} dashboards.
        </div>
      </section>
    </div>
    <section class="panel ledger">
      <div class="ledger-head"><span>SolarWinds</span><span>Mapping split and migration progress</span><span>Datadog</span><span>Migrated</span></div>
      ${Object.entries(cats).map(([k, c]) => {
        const d = delta[k];
        const dl = d ? (d.newly_migrated ? `<div class="delta">+${d.newly_migrated} since last scan</div>` : d.regressed ? `<div class="delta neg">−${d.regressed} since last scan</div>` : "") : "";
        return `<a class="ledger-row" href="#/p/${p.id}/inventory/${k}">
          <div class="side"><b>${fmt(c.total)} ${h(/^[A-Z]{2}/.test(c.label) ? c.label : c.label.toLowerCase())}</b><span>${h(c.source)}<span class="sm-only"> → ${h(c.target)}</span></span></div>
          ${flow(c)}
          <div class="side dd"><b>${h(c.target)}</b><span>${fmt(c.migrated)} found in Datadog</span></div>
          <div class="score"><b>${c.in_scope ? c.readiness + "%" : "—"}</b><div class="small muted">${fmt(c.pending)} pending</div>${dl}</div>
        </a>`;
      }).join("")}
    </section>
    ${scan.warnings?.length ? `<div class="panel warnings"><b>Scan warnings</b><ul>${scan.warnings.map((w) => `<li>${h(w)}</li>`).join("")}</ul></div>` : ""}
  </div>`;
  $("#scanBtn").addEventListener("click", (e) => startScan(e.currentTarget));
  pollRunning(route);
}

/* --------------------------------------------------------------- inventory */
async function viewInventory(cat) {
  const p = state.project, scan = latestScan(), meta = state.meta.categories[cat];
  if (!meta) throw new Error("Unknown inventory category");
  crumbs([{ label: "Migrations", href: "#/" }, { label: p.name, href: `#/p/${p.id}` }, { label: meta.label }]);
  if (!scan) {
    main.innerHTML = `<div class="page empty"><h2>No scan yet</h2><p>Run a scan from the overview to populate the ${h(meta.label.toLowerCase())} inventory.</p><a class="btn" href="#/p/${p.id}">Go to overview</a></div>`;
    return;
  }
  const c = scan.summary.categories[cat];
  const f = { mapping: "", status: "", q: "", offset: 0, limit: 100 };
  main.innerHTML = `
  <div class="page stack">
    <div class="spread">
      <div><h1>${h(meta.label)}</h1><p class="muted" style="margin-top:6px">SolarWinds ${h(meta.source)} → Datadog ${h(meta.target)}. Scan ${scan.id}.</p></div>
      <div class="row">
        <a class="btn" href="/api/scans/${scan.id}/report/${cat}" target="_blank" rel="noopener">Open report</a>
        <a class="btn" href="/api/scans/${scan.id}/export/${cat}.csv">Download CSV</a>
      </div>
    </div>
    <section class="panel" style="overflow:hidden"><div class="kpis">
      <div class="kpi"><b>${fmt(c.total)}</b><span>Discovered</span></div>
      <div class="kpi done"><b>${c.in_scope ? c.readiness + "%" : "—"}</b><span>${fmt(c.migrated)} migrated</span></div>
      <div class="kpi"><b>${fmt(c.pending)}</b><span>Pending</span></div>
      <div class="kpi auto"><b>${fmt(c.auto)}</b><span>Automatic mapping</span></div>
      <div class="kpi review"><b>${fmt(c.review)}</b><span>Needs review</span></div>
      <div class="kpi unsupported"><b>${fmt(c.unsupported)}</b><span>Unsupported</span></div>
    </div></section>
    <section class="panel">
      <div class="filters">
        <input id="fq" type="search" placeholder="Search names, targets, notes" aria-label="Search">
        <select id="fm" aria-label="Mapping"><option value="">All mappings</option><option value="auto">Automatic</option><option value="review">Needs review</option><option value="unsupported">Unsupported</option></select>
        <select id="fs" aria-label="Status"><option value="">Any status</option><option value="migrated">Migrated</option><option value="pending">Pending</option></select>
      </div>
      <div class="table-wrap" id="tbl"></div>
      <div class="pager" id="pager"></div>
    </section>
  </div>`;

  const load = async () => {
    const qs = new URLSearchParams({ category: cat, offset: f.offset, limit: f.limit });
    if (f.mapping) qs.set("mapping", f.mapping);
    if (f.status) qs.set("status", f.status);
    if (f.q) qs.set("q", f.q);
    const r = await api(`/api/scans/${scan.id}/items?${qs}`);
    const keys = [...new Set(r.items.flatMap((i) => Object.keys(i.details)))].slice(0, 5);
    $("#tbl").innerHTML = r.items.length ? `<table><thead><tr>
        <th>SolarWinds object</th><th>Mapping</th><th>Datadog target</th><th>Status</th><th>Found in Datadog</th>
        ${keys.map((k) => `<th>${h(k.charAt(0).toUpperCase() + k.slice(1).replace(/_/g, " "))}</th>`).join("")}</tr></thead><tbody>
      ${r.items.map((i) => `<tr>
        <td class="name">${h(i.source_name)}<div class="sub">${h(i.notes)}</div></td>
        <td><span class="pill ${i.mapping}">${i.mapping === "auto" ? "automatic" : i.mapping === "review" ? "needs review" : "unsupported"}</span></td>
        <td>${h(i.target)}</td>
        <td class="${i.migrated ? "status-migrated" : "status-pending"}">${i.migrated ? "Migrated" : i.mapping === "unsupported" ? "Out of scope" : "Pending"}</td>
        <td class="small">${h(i.dd_match || "")}</td>
        ${keys.map((k) => `<td class="small detail">${h(i.details[k] === true ? "yes" : i.details[k] === false ? "no" : i.details[k])}</td>`).join("")}
      </tr>`).join("")}</tbody></table>`
      : `<div class="empty"><p>No ${h(meta.label.toLowerCase())} match these filters.</p></div>`;
    const end = Math.min(f.offset + f.limit, r.total);
    $("#pager").innerHTML = `<span class="small muted">${r.total ? `${fmt(f.offset + 1)}–${fmt(end)} of ${fmt(r.total)}` : ""}</span>
      <div class="row"><button id="prev" ${f.offset ? "" : "disabled"}>Previous</button><button id="next" ${end < r.total ? "" : "disabled"}>Next</button></div>`;
    $("#prev").onclick = () => { f.offset = Math.max(0, f.offset - f.limit); load(); };
    $("#next").onclick = () => { f.offset += f.limit; load(); };
  };
  let debounce;
  $("#fq").addEventListener("input", (e) => { clearTimeout(debounce); debounce = setTimeout(() => { f.q = e.target.value.trim(); f.offset = 0; load(); }, 250); });
  $("#fm").addEventListener("change", (e) => { f.mapping = e.target.value; f.offset = 0; load(); });
  $("#fs").addEventListener("change", (e) => { f.status = e.target.value; f.offset = 0; load(); });
  await load();
}

/* ------------------------------------------------------------------- rules */
function viewRules() {
  const label = { auto: "automatic", review: "needs review", unsupported: "unsupported", match: "migrated when" };
  main.innerHTML = `
  <div class="page stack">
    <div><h1>Mapping rules</h1><p class="muted" style="margin-top:6px">How each SolarWinds object is classified, and what counts as already migrated.</p></div>
    <section class="panel"><div class="table-wrap"><table><thead><tr><th>SolarWinds</th><th>Datadog</th></tr></thead><tbody>
      ${state.meta.rules.map((r) => `<tr><td>${h(r.source)}</td><td>${h(r.target)}</td></tr>`).join("")}
    </tbody></table></div></section>
    ${state.meta.rules.map((r) => `
      <section class="panel rules-block">
        <div class="panel-head"><h2>${h(r.source)} → ${h(r.target)}</h2></div>
        <div class="table-wrap"><table><thead><tr><th style="width:36%">When</th><th style="width:130px">Result</th><th>Action in Datadog</th></tr></thead><tbody>
        ${r.rules.map((x) => `<tr><td>${h(x.when)}</td><td><span class="pill ${x.result}">${label[x.result]}</span></td><td>${h(x.action)}</td></tr>`).join("")}
        </tbody></table></div>
      </section>`).join("")}
  </div>`;
}

/* ----------------------------------------------------------------- reports */
async function viewReports(scanId) {
  const p = state.project;
  const done = state.scans.filter((s) => s.status === "complete");
  if (!done.length) {
    main.innerHTML = `<div class="page empty"><h2>No reports yet</h2><p>Reports are produced by each completed scan.</p><a class="btn" href="#/p/${p.id}">Go to overview</a></div>`;
    return;
  }
  const scan = done.find((s) => String(s.id) === String(scanId)) || done[0];
  const cats = scan.summary.categories;
  main.innerHTML = `
  <div class="page stack">
    <div class="spread">
      <div><h1>Reports</h1><p class="muted" style="margin-top:6px">Printable HTML reports and CSV exports for a completed scan.</p></div>
      <label class="field" style="min-width:260px">Scan
        <select id="scanPick">${done.map((s) => `<option value="${s.id}" ${s.id === scan.id ? "selected" : ""}>Scan ${s.id}, ${when(s.finished_at)} (${s.summary.overall.readiness}%)</option>`).join("")}</select></label>
    </div>
    <section class="panel report-list">
      <div class="panel-head"><h2>Overall migration assessment</h2></div>
      <div class="item" style="border-top:0">
        <div><b>Assessment report</b><div class="small muted">Readiness, per-category mapping counts, progress since the previous scan, focus areas and warnings.</div></div>
        <div class="row">
          <a class="btn" href="/api/scans/${scan.id}/report" target="_blank" rel="noopener">Open</a>
          <a class="btn" href="/api/scans/${scan.id}/report?download=true">Download HTML</a>
          <a class="btn" href="/api/scans/${scan.id}/export.json">Full JSON</a>
        </div>
      </div>
    </section>
    <section class="panel report-list">
      <div class="panel-head"><h2>Element reports</h2></div>
      ${Object.entries(cats).map(([k, c], i) => `
        <div class="item" ${i === 0 ? 'style="border-top:0"' : ""}>
          <div><b>${h(c.source)} → ${h(c.target)}</b><div class="small muted">${fmt(c.total)} objects, ${c.in_scope ? c.readiness + "% migrated" : "nothing in scope"}, ${fmt(c.review)} need review</div></div>
          <div class="row">
            <a class="btn" href="/api/scans/${scan.id}/report/${k}" target="_blank" rel="noopener">Open</a>
            <a class="btn" href="/api/scans/${scan.id}/export/${k}.csv">CSV</a>
          </div>
        </div>`).join("")}
    </section>
  </div>`;
  $("#scanPick").addEventListener("change", (e) => (location.hash = `#/p/${p.id}/reports/${e.target.value}`));
}

/* ----------------------------------------------------------------- history */
async function viewHistory() {
  const p = state.project;
  const trend = await api(`/api/projects/${p.id}/trend`);
  const cats = state.meta.categories;
  main.innerHTML = `
  <div class="page stack">
    <div class="spread"><div><h1>Scan history</h1><p class="muted" style="margin-top:6px">Each scan re-reads both systems. Compare scans to see what moved.</p></div></div>
    ${trend.length > 1 ? `<section class="panel"><div class="panel-head"><h2>Readiness by category over time</h2></div>
      <div class="table-wrap"><table><thead><tr><th>Category</th>${trend.map((t) => `<th class="num">Scan ${t.scan_id}</th>`).join("")}</tr></thead><tbody>
      <tr><td class="name">Overall</td>${trend.map((t) => `<td class="num"><b>${t.readiness}%</b></td>`).join("")}</tr>
      ${Object.entries(cats).map(([k, m]) => `<tr><td>${h(m.label)}</td>${trend.map((t) => `<td class="num">${t.categories[k] ?? "—"}%</td>`).join("")}</tr>`).join("")}
      </tbody></table></div></section>` : ""}
    <section class="panel"><div class="table-wrap"><table><thead><tr>
      <th>Scan</th><th>Status</th><th>Started</th><th class="num">Objects</th><th class="num">Migrated</th><th class="num">Readiness</th><th class="num">Newly migrated</th><th></th></tr></thead><tbody>
      ${state.scans.map((s) => {
        const o = s.summary?.overall;
        const newly = s.delta ? Object.values(s.delta).reduce((a, d) => a + d.newly_migrated, 0) : null;
        const pill = s.status === "complete" ? "ok" : s.status === "failed" ? "fail" : "run";
        return `<tr><td class="name">${s.id}</td>
          <td><span class="pill ${pill}">${h(s.status)}</span>${s.status === "failed" ? `<div class="sub">${h(s.message)}</div>` : ""}</td>
          <td>${when(s.started_at)}</td><td class="num">${o ? fmt(o.total) : "—"}</td><td class="num">${o ? fmt(o.migrated) : "—"}</td>
          <td class="num">${o ? o.readiness + "%" : "—"}</td><td class="num">${newly === null ? "—" : "+" + fmt(newly)}</td>
          <td class="num">${s.status === "complete" ? `<a href="#/p/${p.id}/reports/${s.id}">Reports</a>` : ""}
            ${s.status !== "running" ? `<button class="danger" data-del="${s.id}" style="margin-left:8px;padding:3px 10px">Delete</button>` : ""}</td></tr>`;
      }).join("") || `<tr><td colspan="8" class="muted">No scans yet.</td></tr>`}
    </tbody></table></div></section>
  </div>`;
  main.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Delete scan ${b.dataset.del}?`)) return;
    await api(`/api/scans/${b.dataset.del}`, { method: "DELETE" });
    toast("Scan deleted.");
    route();
  }));
}

route();
