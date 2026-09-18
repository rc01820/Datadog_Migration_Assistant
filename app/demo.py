"""Demo mode: realistic SolarWinds + Datadog inventories. Each successive scan
migrates more objects, so progress tracking can be exercised without real systems."""
import random

SITES = ["bos", "dal", "sea"]
SERVERS = [("web", "IIS Web Server", "Windows"), ("sql", "SQL Server 2019", "Windows"),
           ("app", "Apache Tomcat", "Linux"), ("dc", "Active Directory 2019 Services", "Windows"),
           ("mq", "RabbitMQ", "Linux"), ("etl", "PowerShell Script Monitor", "Windows"),
           ("ftp", "Legacy FTP Template", "Linux"), ("dns", "DNS Server", "Windows")]
NETWORK = [("core-sw", "Cisco", "Catalyst 9500", "1.3.6.1.4.1.9.1.2494"),
           ("dist-sw", "Cisco", "Catalyst 9300", "1.3.6.1.4.1.9.1.2586"),
           ("edge-rtr", "Juniper", "MX204", "1.3.6.1.4.1.2636.1.1.1.2.150"),
           ("fw", "Palo Alto", "PA-3220", "1.3.6.1.4.1.25461.2.3.38"),
           ("ups", "APC", "Smart-UPS", "")]
ALERTS = [("Node is down", "Node", True), ("High CPU utilization", "Node", True),
          ("High memory utilization", "Node", True), ("Interface is down", "Interface", True),
          ("High interface utilization", "Interface", True), ("Volume almost full", "Volume", True),
          ("Application is critical", "APM: Application", True), ("Component down", "APM: Component", True),
          ("Group is down", "Group", True), ("Custom SWQL: stale backups", "Custom SWQL Alert", True),
          ("Orion poller overloaded", "Orion Server", True), ("License expiring", "Orion License", True),
          ("Old syslog alert", "Node", False), ("Hardware fan failure", "Hardware Sensor", True),
          ("BGP neighbor down", "Node", True), ("Certificate expiring", "APM: Component", True)]


def build(scan_number: int):
    rnd = random.Random(42)
    frac = min(0.18 + 0.17 * scan_number, 0.96)

    nodes, interfaces, volumes, apps, snmp = [], [], [], [], []
    nid = iid = vid = aid = 0
    for site in SITES:
        for role, template, os_name in SERVERS:
            for n in range(1, 3):
                nid += 1
                name = f"{site}-{role}-{n:02d}"
                pm = "Agent" if rnd.random() < 0.55 else "WMI"
                if os_name == "Linux" and n == 2:
                    pm = "SNMP"
                if role == "ftp" and n == 2:
                    pm = "ICMP"
                linux_snmp = pm == "SNMP"
                nodes.append({"id": nid, "name": name, "ip": f"10.{SITES.index(site) + 10}.1.{nid}",
                              "dns": f"{name}.corp.example.com", "sysname": name.upper(),
                              "vendor": "Microsoft" if os_name == "Windows" else "net-snmp",
                              "machine_type": f"{os_name} Server", "polling_method": pm, "status": "Up",
                              "snmp_version": 2 if linux_snmp else 0,
                              "sys_object_id": "1.3.6.1.4.1.8072.3.2.10" if linux_snmp else "",
                              "unmanaged": False})
                if linux_snmp:
                    snmp.append({"id": nid, "name": name, "ip": nodes[-1]["ip"], "snmp_version": 2,
                                 "vendor": "net-snmp", "machine_type": "Linux Server",
                                 "sys_object_id": "1.3.6.1.4.1.8072.3.2.10"})
                for letter in (["C:", "D:"] if os_name == "Windows" else ["/", "/var"]):
                    vid += 1
                    volumes.append({"id": vid, "node_id": nid, "node_name": name, "caption": letter,
                                    "type": "Fixed Disk", "size": rnd.randint(80, 900) * 1024 ** 3})
                vid += 1
                volumes.append({"id": vid, "node_id": nid, "node_name": name, "caption": "Physical Memory",
                                "type": "RAM", "size": 32 * 1024 ** 3})
                if rnd.random() < 0.2:
                    vid += 1
                    volumes.append({"id": vid, "node_id": nid, "node_name": name, "caption": "E:",
                                    "type": "CompactDisk", "size": 0})
                iid += 1
                nic = "eth0" if os_name == "Linux" else "Ethernet0"
                interfaces.append({"id": iid, "node_id": nid, "node_name": name, "name": nic,
                                   "caption": nic, "type": "ethernetCsmacd", "speed": 1e10,
                                   "status": 1, "if_index": 1})
                if pm != "ICMP":
                    aid += 1
                    apps.append({"id": aid, "name": template, "node_id": nid, "node_name": name,
                                 "template": template,
                                 "components": [{"id": aid * 10 + k, "name": c, "type": 0} for k, c in
                                                enumerate(["Service status", "Response time", "Process CPU"])]})
        for role, vendor, model, oid in NETWORK:
            for n in range(1, 3 if role != "ups" else 2):
                nid += 1
                name = f"{site}-{role}-{n:02d}"
                nodes.append({"id": nid, "name": name, "ip": f"10.{SITES.index(site) + 10}.0.{nid}",
                              "dns": "", "sysname": name, "vendor": vendor, "machine_type": model,
                              "polling_method": "SNMP", "status": "Up", "snmp_version": 3 if n == 1 else 2,
                              "sys_object_id": oid, "unmanaged": False})
                snmp.append({"id": nid, "name": name, "ip": nodes[-1]["ip"], "snmp_version": nodes[-1]["snmp_version"],
                             "vendor": vendor, "machine_type": model, "sys_object_id": oid})
                for p in range(1, 13 if "sw" in role else 5):
                    iid += 1
                    port = f"GigabitEthernet1/0/{p}" if vendor == "Cisco" else f"ge-0/0/{p}"
                    interfaces.append({"id": iid, "node_id": nid, "node_name": name, "name": port,
                                       "caption": port, "type": "ethernetCsmacd", "speed": 1e9,
                                       "status": 1, "if_index": p})
    nid += 1
    nodes.append({"id": nid, "name": "saas-status-page", "ip": "203.0.113.10", "dns": "", "sysname": "",
                  "vendor": "", "machine_type": "", "polling_method": "External", "status": "Up",
                  "snmp_version": 0, "sys_object_id": "", "unmanaged": False})
    http_apps = [(f"{site}-web-01", "HTTPS Monitor", f"Portal {site.upper()}", f"https://{site}-portal.example.com/health")
                 for site in SITES]
    http_apps += [("saas-status-page", "HTTP Monitor", "Status page", "http://status.example.com/"),
                  ("saas-status-page", "HTTPS Monitor", "Public API ping", "https://api.example.com/v1/ping"),
                  ("saas-status-page", "HTTPS Monitor", "Customer login", "https://login.example.com/")]
    by_caption = {n["name"]: n for n in nodes}
    proc_apps = [(f"{site}-{role}-01", proc) for site in SITES
                 for role, proc in (("app", "java"), ("web", "w3wp.exe"), ("mq", "beam.smp"))]
    for node_name, proc in proc_apps:
        aid += 1
        node = by_caption[node_name]
        apps.append({"id": aid, "name": f"{proc} process", "node_id": node["id"], "node_name": node_name,
                     "template": "Windows Process Monitor" if proc.endswith(".exe") else "Linux Process Monitor",
                     "components": [{"id": aid * 10, "name": "Process Monitor", "type": 0, "process": proc}]})
    for node_name, template, app_name, url in http_apps:
        aid += 1
        node = by_caption[node_name]
        apps.append({"id": aid, "name": app_name, "node_id": node["id"], "node_name": node_name,
                     "template": template, "components": [
                         {"id": aid * 10, "name": "HTTPS Monitor" if url.startswith("https") else "HTTP Monitor",
                          "type": 0, "url": url}]})

    groups = []
    for gi, (gname, dyn, members) in enumerate([
        ("Boston Datacenter", False, [n for n in nodes if n["name"].startswith("bos")]),
        ("Dallas Datacenter", False, [n for n in nodes if n["name"].startswith("dal")]),
        ("Seattle Datacenter", False, [n for n in nodes if n["name"].startswith("sea")]),
        ("All SQL Servers", True, [n for n in nodes if "-sql-" in n["name"]]),
        ("Core Network", False, [n for n in nodes if "core" in n["name"] or "edge" in n["name"]]),
        ("Payments Service", False, [n for n in nodes if "-app-" in n["name"] or "-mq-" in n["name"]]),
        ("Decommissioned", False, []),
    ], start=1):
        groups.append({"id": gi, "name": gname, "description": "", "dynamic": dyn,
                       "member_count": len(members),
                       "members": [{"name": m["name"], "type": "Orion.Nodes", "id": m["id"]} for m in members]})

    cps = [("NodesCustomProperties", "Environment", "nvarchar", 3, ["prod", "stage", "dev"]),
           ("NodesCustomProperties", "Business_Unit", "nvarchar", 5, ["Payments", "Retail", "Corp IT"]),
           ("NodesCustomProperties", "Owner_Team", "nvarchar", 9, ["NetOps", "DBA", "Platform"]),
           ("NodesCustomProperties", "Site_Code", "nvarchar", 3, ["BOS", "DAL", "SEA"]),
           ("NodesCustomProperties", "Warranty_Expires", "datetime", 60, ["2027-03-31"]),
           ("NodesCustomProperties", "Asset_Tag", "nvarchar", 400, ["A-10021", "A-10022"]),
           ("NodesCustomProperties", "Legacy_Notes", "nvarchar", 0, []),
           ("Interfaces", "Circuit_ID", "nvarchar", 14, ["CKT-8841", "CKT-8842"]),
           ("Interfaces", "Is_Uplink", "bit", 2, ["True", "False"]),
           ("Volumes", "Backup_Policy", "nvarchar", 4, ["daily", "weekly"]),
           ("Alerts", "Runbook_URL", "nvarchar", 12, ["https://wiki/runbook"])]
    custom_properties = [{"id": f"{t}.{f}", "table": t, "field": f, "data_type": dt, "description": "",
                          "value_count": 0 if d == 0 else len(nodes), "distinct_values": d, "samples": s}
                         for t, f, dt, d, s in cps]

    alerts = [{"id": i, "name": n, "description": "", "enabled": en, "severity": 2, "object_type": ot,
               "frequency": 60} for i, (n, ot, en) in enumerate(ALERTS, start=1)]

    by_name = {n["name"]: n for n in nodes}
    deps = []
    did = 0
    for site in SITES:
        for child in ("web-01", "web-02", "app-01", "app-02"):
            did += 1
            p, c = by_name[f"{site}-core-sw-01"], by_name[f"{site}-{child}"]
            deps.append({"id": did, "name": f"{c['name']} depends on {p['name']}", "parent_type": "Node",
                         "parent_id": p["id"], "parent_name": p["name"], "child_type": "Node",
                         "child_id": c["id"], "child_name": c["name"], "auto_managed": True})
    deps.append({"id": did + 1, "name": "Orphaned WAN dependency", "parent_type": "Unknown", "parent_id": None,
                 "parent_name": "swis://old/Orion/Orion.Nodes/NodeID=999", "child_type": "Group", "child_id": 3,
                 "child_name": "Seattle Datacenter", "auto_managed": False})

    sw = {"nodes": nodes, "interfaces": interfaces, "volumes": volumes, "groups": groups,
          "custom_properties": custom_properties, "applications": apps, "alerts": alerts,
          "dependencies": deps, "snmp_devices": snmp}

    # ---------------------------------------------------------------- Datadog
    def migrated(key: str) -> bool:
        return random.Random(f"{key}").random() < frac

    integ = {"IIS": "iis", "SQL": "sqlserver", "Tomcat": "tomcat", "Active": "active_directory",
             "RabbitMQ": "rabbitmq", "DNS": "dns_check"}
    hosts, ndm, tags = [], [], {}
    for n in nodes:
        is_server = n["polling_method"] in ("Agent", "WMI") or n["machine_type"] == "Linux Server"
        if is_server and n["polling_method"] != "ICMP" and migrated(f"h{n['name']}"):
            app_list = ["agent", "ntp", "system"]
            for a in apps:
                if a["node_id"] == n["id"] and migrated(f"a{a['id']}"):
                    app_list += [v for k, v in integ.items() if k in a["template"]]
            t = [f"sw_node_id:{n['id']}"]
            if migrated(f"t{n['name']}"):
                t += ["environment:prod", f"site_code:{n['name'][:3]}", "business_unit:payments"]
            if migrated(f"g{n['name']}"):
                t.append(f"sw_group:{n['name'][:3]}_datacenter")
            hosts.append({"name": n["dns"] or n["name"], "aliases": [n["name"]], "apps": app_list,
                          "sources": ["agent"], "tags": t, "platform": "windows"})
            for tag in t:
                tags.setdefault(tag, []).append(n["name"])
        if n["polling_method"] == "SNMP" and n["machine_type"] != "Linux Server" and migrated(f"d{n['name']}"):
            ifs = [i["name"] for i in interfaces if i["node_id"] == n["id"] and migrated(f"i{i['id']}")]
            ndm.append({"id": f"default:{n['ip']}", "name": n["name"], "ip": n["ip"], "vendor": n["vendor"],
                        "model": n["machine_type"], "status": "ok", "tags": [], "interfaces": ifs})
    monitors = [{"id": 1000 + a["id"], "name": a["name"], "type": "metric alert", "query": "",
                 "tags": [f"sw_alert_id:{a['id']}"]} for a in alerts if migrated(f"m{a['id']}")]
    monitors.append({"id": 999, "name": "Datadog Agent heartbeat", "type": "service check", "query": "", "tags": []})
    for a in apps:
        proc = next((c.get("process") for c in a["components"] if c.get("process")), None)
        if proc and migrated(f"p{a['id']}"):
            monitors.append({"id": 4000 + a["id"], "name": f"{proc} running on {a['node_name']}",
                             "type": "process alert",
                             "query": f'processes("{proc}").over("host:{a["node_name"]}").rollup("count").last(5m) < 1',
                             "tags": []})
    synthetics = []
    for a in apps:
        url = next((c.get("url") for c in a["components"] if c.get("url")), None)
        if not url or not migrated(f"s{a['id']}"):
            continue
        if a["id"] % 2:
            synthetics.append({"id": f"abc-{a['id']:03d}-xyz", "name": f"{a['name']} availability",
                               "type": "http", "url": url.rstrip("/") + "/", "tags": []})
        else:
            monitors.append({"id": 3000 + a["id"], "name": f"{a['name']} http check", "type": "service check",
                             "query": f'"http.can_connect".over("instance:{a["name"]}","url:{url}")'
                                      '.by("host").last(3).count_by_status()', "tags": []})
    dashboards = [{"id": "abc-123", "title": "Core Network Overview"}]
    if frac > 0.5:
        dashboards.append({"id": "def-456", "title": "Payments Service Health"})
    services = []
    if frac > 0.6:
        services = [{"name": "bos-web-01", "depends_on": ["bos-core-sw-01"]},
                    {"name": "bos-app-01", "depends_on": ["bos-core-sw-01"]}]
    dd = {"hosts": hosts, "ndm_devices": ndm, "monitors": monitors, "dashboards": dashboards,
          "tags": tags, "services": services, "synthetics": synthetics}
    return sw, dd
