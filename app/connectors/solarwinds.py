"""SolarWinds Orion connector using the SWIS REST API (port 17774).

Every category query is isolated: if a module (e.g. SAM or NPM) is not
installed, that category is reported as a warning instead of failing the scan.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Callable

import httpx

log = logging.getLogger("dma.solarwinds")

CATEGORIES = [
    "nodes", "interfaces", "volumes", "groups", "custom_properties",
    "applications", "alerts", "dependencies", "snmp_devices",
]

Q_NODES = """
SELECT n.NodeID, n.Caption, n.IPAddress, n.DNS, n.SysName, n.Vendor, n.MachineType,
       n.ObjectSubType, n.Status, n.StatusDescription, n.SNMPVersion, n.SysObjectID,
       n.IOSVersion, n.Unmanaged
FROM Orion.Nodes n
"""
Q_INTERFACES = """
SELECT i.InterfaceID, i.NodeID, i.Node.Caption AS NodeName, i.Name, i.Caption,
       i.TypeDescription, i.Speed, i.Status, i.InterfaceIndex
FROM Orion.NPM.Interfaces i
"""
Q_VOLUMES = """
SELECT v.VolumeID, v.NodeID, v.Node.Caption AS NodeName, v.Caption, v.VolumeType,
       v.VolumeSize, v.VolumePercentUsed
FROM Orion.Volumes v
"""
Q_GROUPS = "SELECT c.ContainerID, c.Name, c.Description, c.Status FROM Orion.Container c"
Q_GROUP_MEMBERS = "SELECT m.ContainerID, m.Name, m.MemberEntityType, m.MemberPrimaryID FROM Orion.ContainerMembers m"
Q_GROUP_DEFS = "SELECT d.ContainerID, d.Definition FROM Orion.ContainerMemberDefinition d"
Q_CP_DEFS = "SELECT cp.Table, cp.Field, cp.DataType, cp.Description FROM Orion.CustomProperty cp"
Q_APPS = """
SELECT a.ApplicationID, a.Name, a.NodeID, a.Node.Caption AS NodeName,
       a.ApplicationTemplateID, t.Name AS TemplateName
FROM Orion.APM.Application a
LEFT JOIN Orion.APM.ApplicationTemplate t ON a.ApplicationTemplateID = t.ApplicationTemplateID
"""
Q_COMPONENTS = "SELECT c.ComponentID, c.Name, c.ApplicationID, c.ComponentType FROM Orion.APM.Component c"
Q_ALERTS = """
SELECT ac.AlertID, ac.Name, ac.Description, ac.Enabled, ac.Severity, ac.ObjectType, ac.Frequency
FROM Orion.AlertConfigurations ac
"""
Q_DEPENDENCIES = "SELECT d.DependencyId, d.Name, d.ParentUri, d.ChildUri, d.AutoManaged FROM Orion.Dependencies d"

_URI_RE = re.compile(r"/(Orion\.[\w.]+)/(\w+)=(\d+)")
_SAFE_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SolarWindsError(Exception):
    pass


class SolarWindsClient:
    def __init__(self, host: str, username: str, password: str, port: int = 17774,
                 verify_ssl: bool = False, timeout: int = 120):
        host = re.sub(r"^https?://", "", (host or "").strip()).rstrip("/")
        if not host:
            raise SolarWindsError("SolarWinds host is required")
        self.host = host
        self.base = f"https://{host}:{int(port or 17774)}/SolarWinds/InformationService/v3/Json"
        self.http = httpx.Client(auth=(username, password), verify=verify_ssl, timeout=timeout)

    def close(self):
        self.http.close()

    def query(self, swql: str, params: dict | None = None) -> list[dict]:
        try:
            r = self.http.post(f"{self.base}/Query", json={"query": swql, "parameters": params or {}})
        except httpx.ConnectError as e:
            raise SolarWindsError(f"Cannot reach {self.host} on the SWIS port: {e}") from e
        except httpx.TimeoutException as e:
            raise SolarWindsError(f"SWIS query timed out on {self.host}") from e
        if r.status_code in (401, 403):
            raise SolarWindsError("SolarWinds rejected the credentials (HTTP %s)" % r.status_code)
        if r.status_code >= 400:
            detail = r.text[:400]
            try:
                detail = r.json().get("Message", detail)
            except ValueError:
                pass
            raise SolarWindsError(f"SWIS returned HTTP {r.status_code}: {detail}")
        return r.json().get("results", [])

    # ------------------------------------------------------------------ test
    def test(self) -> dict:
        count = self.query("SELECT COUNT(n.NodeID) AS C FROM Orion.Nodes n")
        info = {"ok": True, "host": self.host, "nodes": count[0]["C"] if count else 0, "modules": []}
        try:
            mods = self.query("SELECT m.Name, m.Version FROM Orion.InstalledModule m")
            info["modules"] = [f"{m['Name']} {m.get('Version') or ''}".strip() for m in mods]
        except SolarWindsError:
            pass
        return info

    # ------------------------------------------------------------ inventory
    def collect(self, progress: Callable[[int, str], None]) -> tuple[dict, list[str]]:
        inv: dict = {c: [] for c in CATEGORIES}
        warnings: list[str] = []
        steps = [
            ("nodes", self._nodes), ("interfaces", self._interfaces), ("volumes", self._volumes),
            ("groups", self._groups), ("custom_properties", self._custom_properties),
            ("applications", self._applications), ("alerts", self._alerts),
            ("dependencies", self._dependencies),
        ]
        for i, (name, fn) in enumerate(steps):
            progress(int(5 + i * 50 / len(steps)), f"Reading SolarWinds {name.replace('_', ' ')}")
            try:
                inv[name] = fn(inv)
            except SolarWindsError as e:
                warnings.append(f"SolarWinds {name.replace('_', ' ')} skipped: {e}")
                log.warning("category %s failed: %s", name, e)
        inv["snmp_devices"] = [
            {"id": n["id"], "name": n["name"], "ip": n["ip"], "snmp_version": n["snmp_version"],
             "vendor": n["vendor"], "machine_type": n["machine_type"], "sys_object_id": n["sys_object_id"]}
            for n in inv["nodes"] if n["polling_method"] == "SNMP"
        ]
        return inv, warnings

    def _nodes(self, _inv):
        out = []
        for r in self.query(Q_NODES):
            out.append({
                "id": r["NodeID"], "name": r.get("Caption") or "", "ip": r.get("IPAddress") or "",
                "dns": r.get("DNS") or "", "sysname": r.get("SysName") or "",
                "vendor": r.get("Vendor") or "", "machine_type": r.get("MachineType") or "",
                "polling_method": r.get("ObjectSubType") or "", "status": r.get("StatusDescription") or "",
                "snmp_version": r.get("SNMPVersion") or 0, "sys_object_id": r.get("SysObjectID") or "",
                "os_version": r.get("IOSVersion") or "", "unmanaged": bool(r.get("Unmanaged")),
            })
        return out

    def _interfaces(self, _inv):
        return [{
            "id": r["InterfaceID"], "node_id": r["NodeID"], "node_name": r.get("NodeName") or "",
            "name": r.get("Name") or "", "caption": r.get("Caption") or "",
            "type": r.get("TypeDescription") or "", "speed": r.get("Speed") or 0,
            "status": r.get("Status"), "if_index": r.get("InterfaceIndex"),
        } for r in self.query(Q_INTERFACES)]

    def _volumes(self, _inv):
        return [{
            "id": r["VolumeID"], "node_id": r["NodeID"], "node_name": r.get("NodeName") or "",
            "caption": r.get("Caption") or "", "type": r.get("VolumeType") or "",
            "size": r.get("VolumeSize") or 0, "percent_used": r.get("VolumePercentUsed"),
        } for r in self.query(Q_VOLUMES)]

    def _groups(self, _inv):
        members = defaultdict(list)
        try:
            for m in self.query(Q_GROUP_MEMBERS):
                members[m["ContainerID"]].append({"name": m.get("Name"), "type": m.get("MemberEntityType"),
                                                  "id": m.get("MemberPrimaryID")})
        except SolarWindsError:
            pass
        dynamic = set()
        try:
            for d in self.query(Q_GROUP_DEFS):
                if str(d.get("Definition") or "").lower().startswith("filter:"):
                    dynamic.add(d["ContainerID"])
        except SolarWindsError:
            pass
        return [{
            "id": r["ContainerID"], "name": r.get("Name") or "", "description": r.get("Description") or "",
            "dynamic": r["ContainerID"] in dynamic, "member_count": len(members[r["ContainerID"]]),
            "members": members[r["ContainerID"]][:500],
        } for r in self.query(Q_GROUPS)]

    def _custom_properties(self, _inv):
        defs = self.query(Q_CP_DEFS)
        by_table = defaultdict(list)
        for d in defs:
            if _SAFE_FIELD.match(d.get("Field") or ""):
                by_table[d["Table"]].append(d["Field"])
        values: dict[tuple, Counter] = defaultdict(Counter)
        entity_for = {"NodesCustomProperties": "Orion.NodesCustomProperties",
                      "Interfaces": "Orion.NPM.InterfacesCustomProperties",
                      "InterfacesCustomProperties": "Orion.NPM.InterfacesCustomProperties",
                      "Volumes": "Orion.VolumesCustomProperties",
                      "VolumesCustomProperties": "Orion.VolumesCustomProperties"}
        for table, fields in by_table.items():
            entity = entity_for.get(table)
            if not entity:
                continue
            cols = ", ".join(f"cp.{f}" for f in fields)
            try:
                for row in self.query(f"SELECT {cols} FROM {entity} cp"):
                    for f in fields:
                        v = row.get(f)
                        if v not in (None, ""):
                            values[(table, f)][str(v)] += 1
            except SolarWindsError:
                continue
        out = []
        for d in defs:
            c = values.get((d["Table"], d["Field"]), Counter())
            out.append({
                "id": f"{d['Table']}.{d['Field']}", "table": d["Table"], "field": d["Field"],
                "data_type": d.get("DataType") or "", "description": d.get("Description") or "",
                "value_count": sum(c.values()), "distinct_values": len(c),
                "samples": [v for v, _ in c.most_common(8)],
            })
        return out

    def _applications(self, _inv):
        comps = defaultdict(list)
        try:
            for c in self.query(Q_COMPONENTS):
                comps[c["ApplicationID"]].append({"id": c["ComponentID"], "name": c.get("Name") or "",
                                                  "type": c.get("ComponentType")})
        except SolarWindsError:
            pass
        return [{
            "id": r["ApplicationID"], "name": r.get("Name") or "", "node_id": r.get("NodeID"),
            "node_name": r.get("NodeName") or "", "template": r.get("TemplateName") or "",
            "components": comps[r["ApplicationID"]],
        } for r in self.query(Q_APPS)]

    def _alerts(self, _inv):
        return [{
            "id": r["AlertID"], "name": r.get("Name") or "", "description": r.get("Description") or "",
            "enabled": bool(r.get("Enabled")), "severity": r.get("Severity"),
            "object_type": r.get("ObjectType") or "", "frequency": r.get("Frequency"),
        } for r in self.query(Q_ALERTS)]

    def _dependencies(self, inv):
        node_names = {n["id"]: n["name"] for n in inv.get("nodes", [])}
        group_names = {g["id"]: g["name"] for g in inv.get("groups", [])}

        def parse(uri):
            m = _URI_RE.search(uri or "")
            if not m:
                return "Unknown", None, uri or ""
            entity, _, ident = m.groups()
            ident = int(ident)
            if entity == "Orion.Nodes":
                return "Node", ident, node_names.get(ident, f"Node {ident}")
            if entity in ("Orion.Groups", "Orion.Container"):
                return "Group", ident, group_names.get(ident, f"Group {ident}")
            return entity.split(".")[-1], ident, f"{entity} {ident}"

        out = []
        for r in self.query(Q_DEPENDENCIES):
            pt, pid, pn = parse(r.get("ParentUri"))
            ct, cid, cn = parse(r.get("ChildUri"))
            out.append({"id": r["DependencyId"], "name": r.get("Name") or f"{pn} > {cn}",
                        "parent_type": pt, "parent_id": pid, "parent_name": pn,
                        "child_type": ct, "child_id": cid, "child_name": cn,
                        "auto_managed": bool(r.get("AutoManaged"))})
        return out
