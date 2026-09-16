"""Standalone, printable HTML reports plus CSV exports."""
from __future__ import annotations

import csv
import html
import io

from .mapping.engine import ORDER
from .mapping.rules import CATEGORY_META, RULES

E = html.escape

CSS = """
:root{--bg:#17141f;--panel:#201c2b;--line:#342e45;--text:#ece9f3;--muted:#a39cb6;--violet:#a57cf5;
--auto:#3fc6a2;--review:#f0b545;--unsup:#ec6f6f;--done:#7fd46b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.5 "IBM Plex Sans",-apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
main{max-width:1100px;margin:0 auto;padding:40px 28px}
h1{font-size:30px;font-weight:600;margin:0 0 4px;letter-spacing:-.01em}h2{font-size:19px;margin:36px 0 12px}
.sub{color:var(--muted);margin:0 0 28px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--line);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
.kpi{background:var(--panel);padding:16px 18px}.kpi b{display:block;font-size:26px;font-weight:600}
.kpi span{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500}td.n{text-align:right}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:500}
.auto{background:rgba(63,198,162,.15);color:var(--auto)}.review{background:rgba(240,181,69,.15);color:var(--review)}
.unsupported{background:rgba(236,111,111,.15);color:var(--unsup)}.yes{color:var(--done)}.no{color:var(--muted)}
.bar{display:flex;height:8px;border-radius:4px;overflow:hidden;background:var(--line);min-width:120px}
.bar i{display:block}.wrap{overflow-x:auto}
.note{color:var(--muted);font-size:12px}
@media print{body{background:#fff;color:#111}.kpi,main{background:#fff}th,.sub,.note{color:#555}
:root{--line:#ddd;--panel:#fff}}
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{E(title)}</title>"
            f"<style>{CSS}</style></head><body><main>{body}</main></body></html>")


def _kpis(s: dict) -> str:
    cells = [(f"{s['readiness']}%", "Migrated (in scope)"), (s["total"], "Objects discovered"),
             (s["migrated"], "Already in Datadog"), (s["pending"], "Still to migrate"),
             (s["auto"], "Automatic mapping"), (s["review"], "Needs review"),
             (s["unsupported"], "Unsupported"), (f"{s['auto_rate']}%", "Automatic mapping rate")]
    return "<div class='kpis'>" + "".join(f"<div class='kpi'><b>{v}</b><span>{l}</span></div>" for v, l in cells) + "</div>"


def _bar(s: dict) -> str:
    t = max(s["total"], 1)
    return ("<div class='bar'>"
            f"<i style='width:{100*s['auto']/t}%;background:var(--auto)'></i>"
            f"<i style='width:{100*s['review']/t}%;background:var(--review)'></i>"
            f"<i style='width:{100*s['unsupported']/t}%;background:var(--unsup)'></i></div>")


def _header(project: dict, scan: dict) -> str:
    return (f"<p class='sub'>{E(project['name'])}. SolarWinds {E(project['source_host'])} to Datadog "
            f"{E(project['datadog_site'])}. Scan {scan['id']} finished {E((scan.get('finished_at') or '').replace('T', ' ').replace('+00:00', ' UTC'))}.</p>")


def overall_html(project: dict, scan: dict, summary: dict, delta: dict, warnings: list) -> str:
    o = summary["overall"]
    rows = ""
    for cat in ORDER:
        c = summary["categories"][cat]
        d = (delta or {}).get(cat, {})
        change = f"+{d['newly_migrated']}" if d.get("newly_migrated") else ("0" if delta else "—")
        rows += (f"<tr><td>{E(c['label'])}</td><td>{E(c['source'])} → {E(c['target'])}</td>"
                 f"<td class='n'>{c['total']}</td><td class='n'>{c['auto']}</td><td class='n'>{c['review']}</td>"
                 f"<td class='n'>{c['unsupported']}</td><td class='n'>{c['migrated']}</td>"
                 f"<td class='n'>{c['pending']}</td><td class='n'>{c['readiness']}%</td>"
                 f"<td class='n'>{change}</td><td>{_bar(c)}</td></tr>")
    ranked = sorted((summary["categories"][c] for c in ORDER if summary["categories"][c]["in_scope"]),
                    key=lambda c: c["readiness"])
    focus = "".join(f"<li>{E(c['label'])}: {c['pending']} of {c['in_scope']} in-scope objects are not in Datadog yet "
                    f"({c['review']} need manual review).</li>" for c in ranked[:3] if c["pending"])
    warn = "".join(f"<li>{E(w)}</li>" for w in warnings) or "<li>None</li>"
    body = (f"<h1>Migration assessment</h1>{_header(project, scan)}{_kpis(o)}"
            "<h2>By category</h2><div class='wrap'><table><thead><tr><th>Category</th><th>Mapping</th>"
            "<th>Total</th><th>Auto</th><th>Review</th><th>Unsupported</th><th>Migrated</th><th>Pending</th>"
            "<th>Readiness</th><th>Since last scan</th><th>Mapping split</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
            f"<h2>Where to focus next</h2><ul>{focus or '<li>Everything in scope is migrated.</li>'}</ul>"
            f"<h2>Scan warnings</h2><ul>{warn}</ul>"
            "<p class='note'>Readiness = migrated ÷ (total − unsupported). An object counts as migrated when an "
            "equivalent was found in Datadog during this scan.</p>")
    return _page(f"Migration assessment — scan {scan['id']}", body)


def category_html(project: dict, scan: dict, summary: dict, cat: str, items: list, delta: dict) -> str:
    meta = CATEGORY_META[cat]
    c = summary["categories"][cat]
    rules = next(r for r in RULES if r["category"] == cat)["rules"]
    rule_rows = "".join(f"<tr><td>{E(r['when'])}</td><td><span class='pill {r['result'] if r['result'] != 'match' else ''}'>"
                        f"{E(r['result'])}</span></td><td>{E(r['action'])}</td></tr>" for r in rules)
    detail_keys = sorted({k for it in items for k in it["details"]})[:7]
    head = "".join(f"<th>{E(k.replace('_', ' ').capitalize())}</th>" for k in detail_keys)
    rows = ""
    for it in sorted(items, key=lambda x: (x["migrated"], x["mapping"], x["source_name"])):
        det = "".join(f"<td>{E(str(it['details'].get(k, '') if it['details'].get(k) is not None else ''))}</td>"
                      for k in detail_keys)
        rows += (f"<tr><td>{E(str(it['source_name']))}</td><td><span class='pill {it['mapping']}'>{it['mapping']}</span></td>"
                 f"<td>{E(str(it['target']))}</td>"
                 f"<td class='{'yes' if it['migrated'] else 'no'}'>{'Migrated' if it['migrated'] else ('—' if it['mapping'] == 'unsupported' else 'Pending')}</td>"
                 f"<td>{E(it['dd_match'] or '')}</td><td class='note'>{E(it['notes'])}</td>{det}</tr>")
    d = (delta or {}).get(cat)
    progress = ""
    if d:
        names = ", ".join(E(n) for n in d["newly_migrated_names"][:20])
        progress = (f"<h2>Since the previous scan</h2><p>{d['newly_migrated']} newly migrated, {d['regressed']} "
                    f"no longer found in Datadog, {d['added']} added and {d['removed']} removed in SolarWinds.</p>"
                    + (f"<p class='note'>Newly migrated: {names}</p>" if names else ""))
    body = (f"<h1>{E(meta['label'])}: {E(meta['source'])} → {E(meta['target'])}</h1>{_header(project, scan)}"
            f"{_kpis(c)}{progress}<h2>Mapping rules</h2><div class='wrap'><table><thead><tr><th>When</th>"
            f"<th>Result</th><th>Action in Datadog</th></tr></thead><tbody>{rule_rows}</tbody></table></div>"
            f"<h2>Objects ({len(items)})</h2><div class='wrap'><table><thead><tr><th>SolarWinds object</th>"
            f"<th>Mapping</th><th>Datadog target</th><th>Status</th><th>Found in Datadog</th><th>Notes</th>{head}"
            f"</tr></thead><tbody>{rows}</tbody></table></div>")
    return _page(f"{meta['label']} report — scan {scan['id']}", body)


def category_csv(items: list) -> str:
    keys = sorted({k for it in items for k in it["details"]})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["source_id", "source_name", "mapping", "target_type", "target", "migrated", "datadog_match", "notes", *keys])
    for it in items:
        w.writerow([it["source_id"], it["source_name"], it["mapping"], it["target_type"], it["target"],
                    "yes" if it["migrated"] else "no", it["dd_match"] or "", it["notes"],
                    *[it["details"].get(k, "") for k in keys]])
    return buf.getvalue()
