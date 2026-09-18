"""Classifies every SolarWinds object (auto / review / unsupported) and checks
whether an equivalent already exists in Datadog (migrated / pending)."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit
from collections import defaultdict

from .rules import (CATEGORY_META, LINUX_KEYWORDS, LINUX_SYS_OBJECT_ID, SAM_HTTP_COMPONENT_TYPES,
                    SAM_INTEGRATIONS, SAM_PROCESS_COMPONENT_TYPES, SAM_REVIEW_KEYWORDS, WINDOWS_KEYWORDS,
                    WINDOWS_SYS_OBJECT_ID)

ORDER = list(CATEGORY_META)


def norm_host(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        return value.split(".")[0]


def norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def tag_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9_./-]+", "_", (value or "").strip().lower()).strip("_")
    return key if key[:1].isalpha() else f"sw_{key}"


def _oid_under(node: dict, root: str) -> bool:
    oid = str(node.get("sys_object_id") or "").lstrip(".")
    return oid == root or oid.startswith(root + ".")


def _node_text(node: dict) -> str:
    return " ".join(str(node.get(k) or "") for k in ("vendor", "machine_type", "os_version")).lower()


def is_windows_server(node: dict | None) -> bool:
    if not node:
        return False
    return _oid_under(node, WINDOWS_SYS_OBJECT_ID) or any(k in _node_text(node) for k in WINDOWS_KEYWORDS)


def is_linux_server(node: dict | None) -> bool:
    if not node:
        return False
    return _oid_under(node, LINUX_SYS_OBJECT_ID) or any(k in _node_text(node) for k in LINUX_KEYWORDS)


_DEFAULT_PORTS = {"http": 80, "https": 443}
_URL_IN_QUERY = re.compile(r"url:([^\"',}\s)]+)")


def norm_url(value: str) -> str:
    """scheme://host[:port]/path, lowercase host, default port and trailing slash dropped."""
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError:
        return value.lower().rstrip("/")
    scheme = (u.scheme or "http").lower()
    host = (u.hostname or "").lower()
    port_part = f":{port}" if port and port != _DEFAULT_PORTS.get(scheme) else ""
    return f"{scheme}://{host}{port_part}{u.path.rstrip('/')}"


def url_host(value: str) -> str:
    try:
        return (urlsplit(value if "://" in value else "http://" + value).hostname or "").lower()
    except ValueError:
        return ""


def _tokens(value: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (value or "").lower()) if len(t) > 1}


def _item(cat, src_id, name, mapping, target, migrated=False, match=None, notes="", **details):
    return {"category": cat, "source_id": src_id, "source_name": name, "mapping": mapping,
            "target_type": CATEGORY_META[cat]["target"], "target": target,
            "migrated": bool(migrated) and mapping != "unsupported",
            "dd_match": match, "notes": notes, "details": details}


class Index:
    """Lookup structures built once from the Datadog inventory."""

    def __init__(self, dd: dict):
        self.hosts = {}
        for h in dd.get("hosts", []):
            for n in [h["name"], *h.get("aliases", [])]:
                if norm_host(n):
                    self.hosts.setdefault(norm_host(n), h)
        self.ndm_ip = {d["ip"]: d for d in dd.get("ndm_devices", []) if d.get("ip")}
        self.ndm_name = {norm_host(d["name"]): d for d in dd.get("ndm_devices", []) if d.get("name")}
        self.monitors_by_name = {norm_name(m["name"]): m for m in dd.get("monitors", [])}
        self.monitor_tokens = [(_tokens(m["name"]), m) for m in dd.get("monitors", [])]
        self.monitors_by_alert = {}
        for m in dd.get("monitors", []):
            for t in m.get("tags", []):
                if t.startswith("sw_alert_id:"):
                    self.monitors_by_alert[t.split(":", 1)[1]] = m
        self.tag_keys = defaultdict(set)
        for tag in dd.get("tags", {}):
            k, _, v = tag.partition(":")
            self.tag_keys[k.lower()].add(v.lower())
        self.dash_titles = [(d["title"].lower(), d) for d in dd.get("dashboards", [])]
        self.services = {s["name"].lower(): s for s in dd.get("services", []) if s.get("name")}
        # HTTP checks: Synthetic tests plus monitors on http_check metrics.
        self.http_by_url, self.http_by_host, self.http_named = {}, {}, []
        for t in dd.get("synthetics", []):
            label = f"synthetic {t['type']} test {t['id']}: {t['name']}"
            self._add_http(t.get("url"), t["name"], label)
        # Process checks: process monitors and hosts running the Agent process check.
        self.process_monitors, self.process_named = [], []
        for m in dd.get("monitors", []):
            q = m.get("query") or ""
            if m.get("type") == "process alert" or "processes(" in q or "process.up" in q:
                label = f"process monitor #{m['id']}: {m['name']}"
                self.process_monitors.append((f"{m['name']} {q}".lower(), label))
                self.process_named.append((_tokens(m["name"]), label))
            if "http.can_connect" in q or "http.response_time" in q or "http.ssl" in q:
                label = f"http_check monitor #{m['id']}: {m['name']}"
                urls = _URL_IN_QUERY.findall(q) or [""]
                for u in urls:
                    self._add_http(u, m["name"], label)

    def _add_http(self, url, name, label):
        if url:
            self.http_by_url.setdefault(norm_url(url), label)
            self.http_by_host.setdefault(url_host(url), label)
        self.http_named.append((_tokens(name), label))

    def http_check_for(self, urls: list[str], name: str):
        """Returns (match label, quality): exact URL, same hostname, or similar name."""
        for u in urls:
            if norm_url(u) in self.http_by_url:
                return self.http_by_url[norm_url(u)], "exact URL"
        for u in urls:
            h = url_host(u)
            if h and h in self.http_by_host:
                return f"{self.http_by_host[h]} (same hostname)", "same hostname"
        label = _best_by_name(name, self.http_named)
        return (f"{label} (similar name)", "similar name") if label else (None, "")

    def process_check_for(self, processes: list[str], name: str, host: dict | None):
        """Returns (match label, quality): process name, host process check, or similar name."""
        wanted = set()
        for p in processes:
            p = p.strip().lower()
            if p:
                wanted.add(p)
                wanted.add(p.rsplit(".", 1)[0] if p.endswith((".exe", ".sh", ".bat")) else p)
        for text, label in self.process_monitors:
            if any(w in text for w in wanted if len(w) > 2):
                return label, "process name"
        if host and "process" in {a.lower() for a in host.get("apps", [])}:
            return f"host:{host['name']} / process check", "host process check"
        label = _best_by_name(name, self.process_named)
        return (f"{label} (similar name)", "similar name") if label else (None, "")

    def host_for(self, node: dict):
        for key in (node.get("name"), node.get("dns"), node.get("sysname")):
            h = self.hosts.get(norm_host(key))
            if h:
                return h
        return None

    def ndm_for(self, node: dict):
        return self.ndm_ip.get(node.get("ip") or "") or self.ndm_name.get(norm_host(node.get("name")))


# ---------------------------------------------------------------- categories
def map_nodes(sw, ix):
    out = []
    for n in sw.get("nodes", []):
        pm = (n.get("polling_method") or "").upper()
        linux = is_linux_server(n)
        host, dev = ix.host_for(n), ix.ndm_for(n)
        match = f"host:{host['name']}" if host else (f"ndm:{dev['name'] or dev['ip']}" if dev else None)
        if n.get("unmanaged") or pm in ("EXTERNAL", ""):
            m, t, note = "unsupported", "—", "External or unmanaged node; nothing is polled."
        elif pm in ("AGENT", "WMI"):
            m, t, note = "auto", "Datadog Agent host", "Install the Datadog Agent."
        elif pm == "SNMP" and linux:
            m, t, note = "auto", "Datadog Agent host", "Linux server polled by SNMP; install the Datadog Agent."
        elif pm == "SNMP":
            m, t, note = "auto", "NDM device", "Add to NDM autodiscovery."
        else:
            m, t, note = "review", "Synthetic ICMP test", "ICMP-only node; decide on Synthetics or Network Path."
        out.append(_item("nodes", n["id"], n["name"], m, t, bool(match), match, note,
                         ip=n.get("ip"), polling_method=pm, linux_server=linux, vendor=n.get("vendor"),
                         machine_type=n.get("machine_type"), status=n.get("status")))
    return out


def map_interfaces(sw, ix):
    nodes = {n["id"]: n for n in sw.get("nodes", [])}
    out = []
    for i in sw.get("interfaces", []):
        node = nodes.get(i["node_id"])
        pm = (node or {}).get("polling_method", "").upper()
        name = i.get("name") or i.get("caption")
        match = None
        linux = is_linux_server(node)
        if linux and pm in ("AGENT", "SNMP"):
            m, t, note = "review", "system.net.* agent metrics", \
                "Linux server NIC; covered by agent metrics, not NDM interfaces."
            h = ix.host_for(node)
            match = f"host:{h['name']}" if h else None
        elif pm == "SNMP":
            m, t, note = "auto", "NDM interface", "Collected once the device is in NDM."
            dev = ix.ndm_for(node)
            if dev:
                names = {x.lower() for x in dev.get("interfaces", [])}
                if name.lower() in names or (i.get("caption") or "").lower() in names:
                    match = f"ndm:{dev['name'] or dev['ip']} / {name}"
        elif pm in ("AGENT", "WMI"):
            m, t, note = "review", "system.net.* agent metrics", "Server NIC; agent network metrics cover it."
            h = ix.host_for(node)
            match = f"host:{h['name']}" if h else None
        else:
            m, t, note = "unsupported", "—", "Parent node has no interface data source in Datadog."
        out.append(_item("interfaces", i["id"], f"{i.get('node_name')} / {i.get('caption') or name}",
                         m, t, bool(match), match, note, node=i.get("node_name"), polling_method=pm, linux_server=linux,
                         type=i.get("type"), speed=i.get("speed"), if_index=i.get("if_index")))
    return out


def map_volumes(sw, ix):
    nodes = {n["id"]: n for n in sw.get("nodes", [])}
    out = []
    for v in sw.get("volumes", []):
        vt = (v.get("type") or "").lower()
        node = nodes.get(v["node_id"]) or {"name": v.get("node_name")}
        host = ix.host_for(node)
        if any(k in vt for k in ("fixed", "mount")):
            m, t, note = "auto", "system.disk.*", "Agent disk check."
        elif any(k in vt for k in ("ram", "virtual", "memory", "other")):
            m, t, note = "review", "system.mem.* / system.swap.*", "Memory volume, not a disk."
        else:
            m, t, note = "unsupported", "—", "Removable/network/optical volume excluded by the disk check."
        match = f"host:{host['name']}" if host else None
        out.append(_item("volumes", v["id"], f"{v.get('node_name')} / {v.get('caption')}", m, t,
                         bool(match), match, note, node=v.get("node_name"), type=v.get("type"),
                         size_gb=round((v.get("size") or 0) / 1024 ** 3, 1)))
    return out


def map_groups(sw, ix):
    out = []
    values = ix.tag_keys.get("sw_group", set())
    for g in sw.get("groups", []):
        tag = f"sw_group:{tag_key(g['name'])}"
        dash = next((d for title, d in ix.dash_titles if g["name"].lower() in title), None)
        match = tag if tag_key(g["name"]) in values else (f"dashboard:{dash['title']}" if dash else None)
        if g.get("member_count", 0) == 0:
            m, note = "unsupported", "Empty group."
        elif g.get("dynamic"):
            m, note = "review", "Dynamic group; rewrite the filter as a tag query."
        else:
            m, note = "auto", "Tag each member; dashboard optional."
        out.append(_item("groups", g["id"], g["name"], m, tag, bool(match), match, note,
                         members=g.get("member_count", 0), dynamic=g.get("dynamic", False),
                         description=g.get("description")))
    return out


def map_custom_properties(sw, ix):
    out = []
    for cp in sw.get("custom_properties", []):
        key = tag_key(cp["field"])
        dt = (cp.get("data_type") or "").lower()
        if cp.get("value_count", 0) == 0 or cp.get("table") not in (
                "NodesCustomProperties", "Interfaces", "InterfacesCustomProperties", "Volumes",
                "VolumesCustomProperties"):
            m, note = "unsupported", "No values, or property is not on a taggable entity."
        elif "date" in dt or cp.get("distinct_values", 0) > 250:
            m, note = "review", "Date/time or high-cardinality values; poor fit for tags."
        else:
            m, note = "auto", f"{cp.get('distinct_values', 0)} distinct values become tag values."
        match = f"tag key {key}" if key in ix.tag_keys else None
        out.append(_item("custom_properties", cp["id"], cp["field"], m, f"{key}:<value>", bool(match), match,
                         note, table=cp.get("table"), data_type=cp.get("data_type"),
                         values=cp.get("value_count"), distinct=cp.get("distinct_values"),
                         samples=", ".join(cp.get("samples", [])[:5])))
    return out


def _has_word(text: str, keyword: str) -> bool:
    """Whole-word match, so 'custom' does not match 'Customer' and 'web' not 'webhook'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text.lower()) is not None


def _integration_for(text: str):
    for keys, integ in SAM_INTEGRATIONS:
        if any(_has_word(text, k) for k in keys):
            return integ
    return None


_HTTP_NAME = re.compile(r"\bhttps?\b", re.I)
_PROCESS_NAME = re.compile(r"\bprocess(es)?\b", re.I)


def _best_by_name(name: str, candidates: list[tuple[set, str]]):
    tk = _tokens(name)
    if not tk:
        return None
    score, label = max(((len(tk & t) / len(tk | t), lbl) for t, lbl in candidates if t),
                       default=(0, None), key=lambda x: x[0])
    return label if score >= 0.8 else None


def process_component_reason(c: dict) -> str | None:
    """Why a SAM component is considered a process monitor, or None."""
    if c.get("process"):
        return "component process setting"
    try:
        if SAM_PROCESS_COMPONENT_TYPES and int(c.get("type")) in SAM_PROCESS_COMPONENT_TYPES:
            return "component type"
    except (TypeError, ValueError):
        pass
    if _PROCESS_NAME.search(c.get("name") or ""):
        return "component name"
    return None


def http_component_reason(c: dict) -> str | None:
    """Why a SAM component is considered an HTTP/HTTPS monitor, or None."""
    if str(c.get("url") or "").lower().startswith(("http://", "https://")):
        return "component URL"
    try:
        if SAM_HTTP_COMPONENT_TYPES and int(c.get("type")) in SAM_HTTP_COMPONENT_TYPES:
            return "component type"
    except (TypeError, ValueError):
        pass
    if _HTTP_NAME.search(c.get("name") or ""):
        return "component name"
    return None


def map_applications(sw, ix):
    nodes = {n["id"]: n for n in sw.get("nodes", [])}
    out = []
    for a in sw.get("applications", []):
        text = f"{a.get('template')} {a.get('name')}"
        integ = _integration_for(text)
        comps = a.get("components", [])
        reasons = [r for r in (http_component_reason(c) for c in comps) if r]
        detected_by = "template/application name" if integ == "http_check" else \
            (reasons[0] if integ is None and reasons else None)
        if detected_by:
            urls = [c["url"] for c in comps
                    if str(c.get("url") or "").lower().startswith(("http://", "https://"))]
            match, quality = ix.http_check_for(urls, a["name"])
            note = "HTTP(S) monitor; create a Synthetic HTTP test or http_check monitor. No host required."
            if not urls:
                note += " No URL was read from SolarWinds, so matching used the name only."
            out.append(_item("applications", a["id"], f"{a.get('node_name')} / {a['name']}", "auto",
                             "Synthetic HTTP test / http_check monitor", bool(match), match, note,
                             node=a.get("node_name"), template=a.get("template"), urls=", ".join(urls),
                             detected_by=detected_by, match_quality=quality,
                             components=len(comps), component_names=", ".join(c["name"] for c in comps[:6])))
            continue

        proc_reasons = [r for r in (process_component_reason(c) for c in comps) if r]
        proc_detected = "template/application name" if integ == "process" else \
            (proc_reasons[0] if integ is None and proc_reasons else None)
        if proc_detected:
            names = [c["process"] for c in comps if c.get("process")]
            host = ix.host_for(nodes.get(a.get("node_id")) or {"name": a.get("node_name")})
            match, quality = ix.process_check_for(names, a["name"], host)
            note = ("Process monitor; use the Agent process check (process.d / Live Processes) "
                    "and a process monitor to alert.")
            if not names:
                note += " No process name was read from SolarWinds, so matching used the name only."
            out.append(_item("applications", a["id"], f"{a.get('node_name')} / {a['name']}", "auto",
                             "Process check / process monitor", bool(match), match, note,
                             node=a.get("node_name"), template=a.get("template"),
                             processes=", ".join(names), detected_by=proc_detected, match_quality=quality,
                             components=len(comps), component_names=", ".join(c["name"] for c in comps[:6])))
            continue
        if integ:
            m, t, note = "auto", integ, f"Enable the {integ} integration."
        elif any(_has_word(text, k) for k in SAM_REVIEW_KEYWORDS):
            m, t, note = "review", "Custom Agent check", "Script or custom template; rebuild as a custom check."
        else:
            m, t = "unsupported", "—"
            note = (f"No Datadog equivalent identified for template '{a.get('template')}' "
                    f"(components: {', '.join(c['name'] for c in comps[:4]) or 'none read'}).")
        host = ix.host_for(nodes.get(a.get("node_id")) or {"name": a.get("node_name")})
        match = None
        if host and integ:
            apps = {x.lower() for x in host.get("apps", [])}
            if integ in apps or integ.replace("_check", "") in apps:
                match = f"host:{host['name']} / {integ}"
        out.append(_item("applications", a["id"], f"{a.get('node_name')} / {a['name']}", m, t,
                         bool(match), match, note, node=a.get("node_name"), template=a.get("template"), urls="", detected_by="",
                         match_quality="host integration" if match else "",
                         components=len(comps), component_names=", ".join(c["name"] for c in comps[:6])))
    return out


def map_alerts(sw, ix):
    out = []
    for al in sw.get("alerts", []):
        ot = (al.get("object_type") or "").lower()
        if not al.get("enabled"):
            m, note = "review", "Disabled in SolarWinds; confirm whether to migrate."
        elif ot in ("node", "interface", "volume"):
            m, note = "auto", "Metric or service-check monitor."
        elif any(k in ot for k in ("orion", "license", "engine", "poller", "database")):
            m, note = "unsupported", "Platform-internal alert."
        else:
            m, note = "review", f"'{al.get('object_type')}' trigger logic needs manual translation."
        mon = ix.monitors_by_alert.get(str(al["id"])) or ix.monitors_by_name.get(norm_name(al["name"]))
        if not mon:
            tk = _tokens(al["name"])
            if tk:
                best = max(((len(tk & mt) / len(tk | mt), mm) for mt, mm in ix.monitor_tokens if mt),
                           default=(0, None), key=lambda x: x[0])
                mon = best[1] if best[0] >= 0.8 else None
        match = f"monitor #{mon['id']}: {mon['name']}" if mon else None
        out.append(_item("alerts", al["id"], al["name"], m, f"[SW] {al['name']}", bool(mon), match, note,
                         object_type=al.get("object_type"), enabled=al.get("enabled"),
                         severity=al.get("severity")))
    return out


def map_dependencies(sw, ix):
    out = []
    for d in sw.get("dependencies", []):
        if d.get("parent_id") is None or d.get("child_id") is None:
            m, note = "unsupported", "Parent or child could not be resolved."
        else:
            m, note = "review", "Model with dependsOn or a composite monitor."
        svc = ix.services.get(norm_host(d.get("child_name")))
        match = None
        if svc and norm_host(d.get("parent_name")) in {x.lower() for x in svc.get("depends_on", [])}:
            match = f"service:{svc['name']} dependsOn {d['parent_name']}"
        out.append(_item("dependencies", d["id"], d["name"], m,
                         f"{norm_host(d.get('child_name'))} dependsOn {norm_host(d.get('parent_name'))}",
                         bool(match), match, note, parent=f"{d.get('parent_type')}: {d.get('parent_name')}",
                         child=f"{d.get('child_type')}: {d.get('child_name')}"))
    return out


def map_snmp_devices(sw, ix):
    nodes = {n["id"]: n for n in sw.get("nodes", [])}
    labels = {1: "v1", 2: "v2c", 3: "v3"}
    out = []
    for s in sw.get("snmp_devices", []):
        node = {**s, **nodes.get(s["id"], {})}
        version = labels.get(int(s.get("snmp_version") or 0))
        windows, linux = is_windows_server(node), is_linux_server(node)
        target = "NDM device"
        if windows or linux:
            m, target = "unsupported", "— (Agent host, see Nodes)"
            note = f"{'Windows' if windows else 'Linux'} server; not mapped to NDM. Migrated as an Agent host."
        elif not version:
            m, note = "review", "SNMP version not recognized; confirm v1, v2c or v3."
        elif not s.get("sys_object_id"):
            m, note = "review", "No sysObjectID recorded; confirm an NDM profile exists."
        else:
            m, note = "auto", "Add to NDM with a matching profile."
        dev = ix.ndm_for(s)
        match = f"ndm:{dev['name'] or dev['ip']}" if dev else None
        out.append(_item("snmp_devices", s["id"], s["name"], m, target, bool(match), match, note,
                         ip=s.get("ip"), snmp_version=version or str(s.get("snmp_version") or ""),
                         server_os="Windows" if windows else "Linux" if linux else "",
                         vendor=s.get("vendor"), model=s.get("machine_type"), sys_object_id=s.get("sys_object_id")))
    return out


MAPPERS = {
    "nodes": map_nodes, "interfaces": map_interfaces, "volumes": map_volumes, "groups": map_groups,
    "custom_properties": map_custom_properties, "applications": map_applications, "alerts": map_alerts,
    "dependencies": map_dependencies, "snmp_devices": map_snmp_devices,
}


def summarize(items: list[dict]) -> dict:
    s = {"total": len(items), "auto": 0, "review": 0, "unsupported": 0, "migrated": 0, "pending": 0}
    for it in items:
        s[it["mapping"]] += 1
        if it["mapping"] != "unsupported":
            s["migrated" if it["migrated"] else "pending"] += 1
    in_scope = s["total"] - s["unsupported"]
    s["in_scope"] = in_scope
    s["readiness"] = round(100 * s["migrated"] / in_scope, 1) if in_scope else 100.0
    s["auto_rate"] = round(100 * s["auto"] / s["total"], 1) if s["total"] else 0.0
    return s


def run(sw: dict, dd: dict) -> tuple[dict, dict]:
    ix = Index(dd)
    results = {cat: MAPPERS[cat](sw, ix) for cat in ORDER}
    per_cat = {cat: {**summarize(results[cat]), **CATEGORY_META[cat]} for cat in ORDER}
    overall = summarize([it for cat in ORDER for it in results[cat]])
    overall["datadog"] = {k: len(v) for k, v in dd.items()}
    return {"overall": overall, "categories": per_cat}, results


def compare(prev_results: dict | None, results: dict) -> dict:
    """Delta between two scans: newly migrated, regressed, added, removed."""
    if not prev_results:
        return {}
    out = {}
    for cat in ORDER:
        before = {str(i["source_id"]): i for i in prev_results.get(cat, [])}
        after = {str(i["source_id"]): i for i in results.get(cat, [])}
        newly = [a["source_name"] for k, a in after.items() if a["migrated"] and k in before and not before[k]["migrated"]]
        regressed = [a["source_name"] for k, a in after.items() if not a["migrated"] and k in before and before[k]["migrated"]]
        out[cat] = {"newly_migrated": len(newly), "regressed": len(regressed),
                    "added": len(after.keys() - before.keys()), "removed": len(before.keys() - after.keys()),
                    "newly_migrated_names": newly[:50], "regressed_names": regressed[:50]}
    return out
