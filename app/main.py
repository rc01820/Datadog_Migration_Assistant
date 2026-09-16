"""Datadog Migration Assistant — API server."""
from __future__ import annotations

import json
import logging
import os
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import crypto, db, demo, reports
from .connectors.datadog import SITES, DatadogClient, DatadogError
from .connectors.solarwinds import SolarWindsClient, SolarWindsError
from .mapping import engine
from .mapping.rules import CATEGORY_META, RULES

logging.basicConfig(level=os.getenv("DMA_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("dma")
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Datadog Migration Assistant", version="1.0.0")
db.init()
# A scan thread cannot survive a restart; release any scan left "running".
db.execute("UPDATE scans SET status='failed', message='Interrupted by an application restart', "
           "finished_at=? WHERE status='running'", (db.now(),))

TOOLS = [
    {"id": "solarwinds", "name": "SolarWinds Orion", "available": True,
     "detail": "Nodes, interfaces, volumes, groups, custom properties, SAM, alerts, dependencies, SNMP."},
    {"id": "zabbix", "name": "Zabbix", "available": False, "detail": "Planned"},
    {"id": "checkmk", "name": "Checkmk", "available": False, "detail": "Planned"},
    {"id": "newrelic", "name": "New Relic", "available": False, "detail": "Planned"},
    {"id": "nagiosxi", "name": "Nagios XI", "available": False, "detail": "Planned"},
    {"id": "prtg", "name": "PRTG", "available": False, "detail": "Planned"},
]


# ------------------------------------------------------------------- models
class SolarWindsConfig(BaseModel):
    host: str = ""
    port: int = 17774
    username: str = ""
    password: str = ""
    verify_ssl: bool = False
    demo: bool = False


class DatadogConfig(BaseModel):
    site: str = "datadoghq.com"
    api_key: str = ""
    app_key: str = ""
    demo: bool = False


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_tool: str = "solarwinds"
    source: SolarWindsConfig
    datadog: DatadogConfig


class TestIn(BaseModel):
    project_id: int | None = None
    source: SolarWindsConfig | None = None
    datadog: DatadogConfig | None = None


# ------------------------------------------------------------------ helpers
def _project(pid: int, secrets: bool = False) -> dict:
    row = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    if not row:
        raise HTTPException(404, "Migration project not found")
    src, dd = crypto.decrypt(row["source_config"]), crypto.decrypt(row["datadog_config"])
    out = {"id": row["id"], "name": row["name"], "source_tool": row["source_tool"],
           "created_at": row["created_at"], "updated_at": row["updated_at"],
           "source": src if secrets else crypto.mask(src), "datadog": dd if secrets else crypto.mask(dd)}
    out["source_host"] = "demo" if src.get("demo") else src.get("host", "")
    out["datadog_site"] = "demo" if dd.get("demo") else dd.get("site", "")
    return out


def _sw_client(cfg: dict) -> SolarWindsClient:
    return SolarWindsClient(cfg.get("host"), cfg.get("username"), cfg.get("password"),
                            cfg.get("port") or 17774, cfg.get("verify_ssl", False))


def _dd_client(cfg: dict) -> DatadogClient:
    return DatadogClient(cfg.get("site"), cfg.get("api_key"), cfg.get("app_key"))


def _scan(sid: int) -> dict:
    row = db.one("SELECT * FROM scans WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, "Scan not found")
    return row


def _latest_done(pid: int, before: int | None = None) -> dict | None:
    if before:
        return db.one("SELECT * FROM scans WHERE project_id=? AND status='complete' AND id<? ORDER BY id DESC LIMIT 1",
                      (pid, before))
    return db.one("SELECT * FROM scans WHERE project_id=? AND status='complete' ORDER BY id DESC LIMIT 1", (pid,))


def _scan_public(row: dict) -> dict:
    summary = db.jload(row["summary"], {})
    return {"id": row["id"], "project_id": row["project_id"], "status": row["status"],
            "progress": row["progress"], "message": row["message"], "started_at": row["started_at"],
            "finished_at": row["finished_at"], "summary": summary.get("scores"),
            "delta": summary.get("delta"), "warnings": db.jload(row["warnings"], [])}


# --------------------------------------------------------------- scan worker
def _run_scan(sid: int, pid: int):
    def progress(pct, msg):
        db.execute("UPDATE scans SET progress=?, message=? WHERE id=?", (pct, msg, sid))

    try:
        p = _project(pid, secrets=True)
        src, ddc = p["source"], p["datadog"]
        warnings = []
        scan_no = db.one("SELECT COUNT(*) AS c FROM scans WHERE project_id=? AND status='complete'", (pid,))["c"]
        demo_sw, demo_dd = demo.build(scan_no) if (src.get("demo") or ddc.get("demo")) else (None, None)

        if src.get("demo"):
            progress(30, "Reading demo SolarWinds inventory")
            sw = demo_sw
        else:
            client = _sw_client(src)
            try:
                sw, w = client.collect(progress)
                warnings += w
            finally:
                client.close()

        if ddc.get("demo"):
            progress(75, "Reading demo Datadog inventory")
            dd = demo_dd
        else:
            client = _dd_client(ddc)
            try:
                dd, w = client.collect(progress)
                warnings += w
            finally:
                client.close()

        progress(92, "Mapping objects and comparing with Datadog")
        scores, results = engine.run(sw, dd)
        prev = _latest_done(pid, before=sid)
        delta = engine.compare(db.jload(prev["results"]) if prev else None, results)
        db.execute("UPDATE scans SET status='complete', progress=100, message=?, finished_at=?, summary=?, "
                   "results=?, warnings=? WHERE id=?",
                   ("Scan complete", db.now(), json.dumps({"scores": scores, "delta": delta,
                                                            "previous_scan": prev["id"] if prev else None}),
                    json.dumps(results), json.dumps(warnings), sid))
        log.info("scan %s complete: %s objects, readiness %s%%", sid, scores["overall"]["total"],
                 scores["overall"]["readiness"])
    except (SolarWindsError, DatadogError) as e:
        db.execute("UPDATE scans SET status='failed', message=?, finished_at=? WHERE id=?", (str(e), db.now(), sid))
    except Exception as e:  # noqa: BLE001
        log.error("scan %s crashed: %s", sid, traceback.format_exc())
        db.execute("UPDATE scans SET status='failed', message=?, finished_at=? WHERE id=?",
                   (f"Unexpected error: {e}", db.now(), sid))


# --------------------------------------------------------------------- API
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/meta")
def meta():
    return {"tools": TOOLS, "datadog_sites": SITES, "categories": CATEGORY_META, "rules": RULES}


@app.get("/api/projects")
def list_projects():
    out = []
    for r in db.query("SELECT id FROM projects ORDER BY updated_at DESC"):
        p = _project(r["id"])
        last = _latest_done(p["id"])
        p["last_scan"] = _scan_public(last) if last else None
        out.append(p)
    return out


@app.post("/api/projects")
def create_project(body: ProjectIn):
    if body.source_tool != "solarwinds":
        raise HTTPException(400, "Only SolarWinds is supported in this version")
    pid = db.execute("INSERT INTO projects(name, source_tool, source_config, datadog_config, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?)",
                     (body.name, body.source_tool, crypto.encrypt(body.source.model_dump()),
                      crypto.encrypt(body.datadog.model_dump()), db.now(), db.now()))
    return _project(pid)


@app.get("/api/projects/{pid}")
def get_project(pid: int):
    return _project(pid)


@app.put("/api/projects/{pid}")
def update_project(pid: int, body: ProjectIn):
    old = _project(pid, secrets=True)
    src = crypto.merge_secrets(body.source.model_dump(), old["source"])
    dd = crypto.merge_secrets(body.datadog.model_dump(), old["datadog"])
    db.execute("UPDATE projects SET name=?, source_config=?, datadog_config=?, updated_at=? WHERE id=?",
               (body.name, crypto.encrypt(src), crypto.encrypt(dd), db.now(), pid))
    return _project(pid)


@app.delete("/api/projects/{pid}")
def delete_project(pid: int):
    _project(pid)
    db.execute("DELETE FROM scans WHERE project_id=?", (pid,))
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    return {"deleted": pid}


@app.post("/api/test/{target}")
def test_connection(target: str, body: TestIn):
    stored = _project(body.project_id, secrets=True) if body.project_id else {"source": {}, "datadog": {}}
    try:
        if target == "solarwinds":
            cfg = crypto.merge_secrets((body.source or SolarWindsConfig()).model_dump(), stored["source"])
            if cfg.get("demo"):
                return {"ok": True, "host": "demo", "nodes": len(demo.build(0)[0]["nodes"]),
                        "modules": ["Demo NPM", "Demo SAM"]}
            c = _sw_client(cfg)
            try:
                return c.test()
            finally:
                c.close()
        if target == "datadog":
            cfg = crypto.merge_secrets((body.datadog or DatadogConfig()).model_dump(), stored["datadog"])
            if cfg.get("demo"):
                return {"ok": True, "site": "demo", "region": "Demo", "hosts": len(demo.build(0)[1]["hosts"])}
            c = _dd_client(cfg)
            try:
                return c.test()
            finally:
                c.close()
    except (SolarWindsError, DatadogError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)
    raise HTTPException(404, "Unknown connection type")


@app.post("/api/projects/{pid}/scans")
def start_scan(pid: int):
    _project(pid)
    running = db.one("SELECT id FROM scans WHERE project_id=? AND status='running'", (pid,))
    if running:
        raise HTTPException(409, f"Scan {running['id']} is still running")
    sid = db.execute("INSERT INTO scans(project_id, status, progress, message, started_at) VALUES (?,?,?,?,?)",
                     (pid, "running", 1, "Starting scan", db.now()))
    threading.Thread(target=_run_scan, args=(sid, pid), daemon=True).start()
    return _scan_public(_scan(sid))


@app.get("/api/projects/{pid}/scans")
def list_scans(pid: int):
    rows = db.query("SELECT id, project_id, status, progress, message, started_at, finished_at, summary, warnings "
                    "FROM scans WHERE project_id=? ORDER BY id DESC", (pid,))
    return [_scan_public(r) for r in rows]


@app.get("/api/projects/{pid}/trend")
def trend(pid: int):
    rows = db.query("SELECT id, finished_at, summary FROM scans WHERE project_id=? AND status='complete' ORDER BY id",
                    (pid,))
    out = []
    for r in rows:
        s = db.jload(r["summary"], {}).get("scores", {})
        out.append({"scan_id": r["id"], "finished_at": r["finished_at"],
                    "readiness": s.get("overall", {}).get("readiness", 0),
                    "migrated": s.get("overall", {}).get("migrated", 0),
                    "pending": s.get("overall", {}).get("pending", 0),
                    "categories": {k: v.get("readiness") for k, v in s.get("categories", {}).items()}})
    return out


@app.get("/api/scans/{sid}")
def get_scan(sid: int):
    return _scan_public(_scan(sid))


@app.delete("/api/scans/{sid}")
def delete_scan(sid: int):
    _scan(sid)
    db.execute("DELETE FROM scans WHERE id=?", (sid,))
    return {"deleted": sid}


@app.get("/api/scans/{sid}/items")
def scan_items(sid: int, category: str, mapping: str | None = None, status: str | None = None,
               q: str | None = None, offset: int = 0, limit: int = Query(100, le=1000)):
    if category not in CATEGORY_META:
        raise HTTPException(404, "Unknown category")
    items = db.jload(_scan(sid)["results"], {}).get(category, [])
    if mapping:
        items = [i for i in items if i["mapping"] == mapping]
    if status == "migrated":
        items = [i for i in items if i["migrated"]]
    elif status == "pending":
        items = [i for i in items if not i["migrated"] and i["mapping"] != "unsupported"]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in json.dumps(i).lower()]
    return {"total": len(items), "items": items[offset: offset + limit]}


def _report_ctx(sid: int):
    scan = _scan(sid)
    if scan["status"] != "complete":
        raise HTTPException(409, "Scan is not complete")
    return (_project(scan["project_id"]), scan, db.jload(scan["summary"], {}),
            db.jload(scan["results"], {}), db.jload(scan["warnings"], []))


@app.get("/api/scans/{sid}/report", response_class=HTMLResponse)
def overall_report(sid: int, download: bool = False):
    p, scan, summ, _, warns = _report_ctx(sid)
    html = reports.overall_html(p, scan, summ["scores"], summ.get("delta"), warns)
    return _maybe_download(html, f"migration-assessment-scan{sid}.html", "text/html", download)


@app.get("/api/scans/{sid}/report/{category}", response_class=HTMLResponse)
def category_report(sid: int, category: str, download: bool = False):
    if category not in CATEGORY_META:
        raise HTTPException(404, "Unknown category")
    p, scan, summ, res, _ = _report_ctx(sid)
    html = reports.category_html(p, scan, summ["scores"], category, res.get(category, []), summ.get("delta"))
    return _maybe_download(html, f"{category}-report-scan{sid}.html", "text/html", download)


@app.get("/api/scans/{sid}/export/{category}.csv")
def category_csv(sid: int, category: str):
    if category not in CATEGORY_META:
        raise HTTPException(404, "Unknown category")
    _, _, _, res, _ = _report_ctx(sid)
    return _maybe_download(reports.category_csv(res.get(category, [])), f"{category}-scan{sid}.csv", "text/csv", True)


@app.get("/api/scans/{sid}/export.json")
def export_json(sid: int):
    p, scan, summ, res, warns = _report_ctx(sid)
    body = json.dumps({"project": {k: p[k] for k in ("name", "source_host", "datadog_site")},
                       "scan": {"id": sid, "finished_at": scan["finished_at"]},
                       "summary": summ, "warnings": warns, "results": res}, indent=2)
    return _maybe_download(body, f"migration-scan{sid}.json", "application/json", True)


def _maybe_download(content: str, filename: str, media: str, download: bool) -> Response:
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if download else {}
    return Response(content, media_type=media, headers=headers)


# ------------------------------------------------------------------ static
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
