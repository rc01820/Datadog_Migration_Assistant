"""SolarWinds -> Datadog mapping rules. These drive both the engine and the
Mapping Rules page, so the documentation can never drift from behaviour."""

CATEGORY_META = {
    "nodes": {"label": "Nodes", "source": "Node", "target": "Host / Device"},
    "interfaces": {"label": "Interfaces", "source": "Interface", "target": "NDM Interface"},
    "volumes": {"label": "Volumes", "source": "Volume", "target": "Host disk check"},
    "groups": {"label": "Groups", "source": "Group", "target": "Tag / Dashboard"},
    "custom_properties": {"label": "Custom properties", "source": "Custom Property", "target": "Tag"},
    "applications": {"label": "Applications", "source": "SAM Monitor", "target": "Integration / Check"},
    "alerts": {"label": "Alerts", "source": "Alert", "target": "Monitor"},
    "dependencies": {"label": "Dependencies", "source": "Dependency", "target": "Service relationship"},
    "snmp_devices": {"label": "SNMP devices", "source": "SNMP device", "target": "NDM Device"},
}

RULES = [
    {"category": "nodes", "source": "Node", "target": "Host / Device", "rules": [
        {"when": "Polled by Agent or WMI", "result": "auto", "action": "Install the Datadog Agent; the node becomes a Host."},
        {"when": "Polled by SNMP and is a Linux server", "result": "auto", "action": "Install the Datadog Agent; the node becomes a Host."},
        {"when": "Polled by SNMP (network devices and other non-Linux systems)", "result": "auto", "action": "Add to NDM autodiscovery or snmp.d/conf.yaml; becomes an NDM Device."},
        {"when": "ICMP-only", "result": "review", "action": "No agent path. Use a Synthetic ICMP test or a Network Path check."},
        {"when": "External or unmanaged node", "result": "unsupported", "action": "Nothing is polled today, so there is nothing to migrate."},
        {"when": "Migrated when", "result": "match", "action": "Caption, DNS or SysName matches a Datadog host name or alias, or the IP matches an NDM device."},
    ]},
    {"category": "interfaces", "source": "Interface", "target": "NDM Interface", "rules": [
        {"when": "Parent node polled by Agent or SNMP and is a Linux server", "result": "review", "action": "Covered by agent metrics (system.net.*), not NDM interfaces."},
        {"when": "Parent node polled by SNMP (not a Linux server)", "result": "auto", "action": "Collected automatically once the device is in NDM."},
        {"when": "Parent node polled by Agent/WMI (not a Linux server)", "result": "review", "action": "Covered by system.net.* agent metrics, not NDM interfaces."},
        {"when": "Parent node ICMP-only or missing", "result": "unsupported", "action": "No interface data source in Datadog."},
        {"when": "Migrated when", "result": "match", "action": "SNMP network device: its NDM device reports an interface with the same name. Linux or Agent/WMI server: the parent node exists as a Datadog host."},
    ]},
    {"category": "volumes", "source": "Volume", "target": "Host disk check", "rules": [
        {"when": "Fixed disk or mount point", "result": "auto", "action": "Covered by the Agent disk check (system.disk.*)."},
        {"when": "RAM, virtual or other memory volume", "result": "review", "action": "Maps to system.mem.* or system.swap.* rather than disk."},
        {"when": "Removable, network or CD volume", "result": "unsupported", "action": "Excluded by the disk check by default."},
        {"when": "Migrated when", "result": "match", "action": "The parent node already exists as a Datadog host."},
    ]},
    {"category": "groups", "source": "Group", "target": "Tag / Dashboard", "rules": [
        {"when": "Static group", "result": "auto", "action": "Tag members with sw_group:<name>; optionally build a dashboard."},
        {"when": "Dynamic group (filter query)", "result": "review", "action": "Rewrite the filter as a tag query in Datadog."},
        {"when": "Empty group", "result": "unsupported", "action": "No members to tag."},
        {"when": "Migrated when", "result": "match", "action": "A sw_group:<name> tag exists or a dashboard title contains the group name."},
    ]},
    {"category": "custom_properties", "source": "Custom Property", "target": "Tag", "rules": [
        {"when": "Text, number or yes/no property with values", "result": "auto", "action": "Becomes tag key <property>:<value> on the host or device."},
        {"when": "Date/time property, or more than 250 distinct values", "result": "review", "action": "High-cardinality or temporal values make poor tags; consider host metadata."},
        {"when": "Property has no values, or is not on Nodes/Interfaces/Volumes", "result": "unsupported", "action": "Nothing to carry over as a tag."},
        {"when": "Migrated when", "result": "match", "action": "A Datadog host tag with the normalized key exists."},
    ]},
    {"category": "applications", "source": "SAM Monitor", "target": "Integration / Check", "rules": [
        {"when": "Template matches a known technology (IIS, SQL Server, Apache, HTTP, TCP port, Windows service, process and more)", "result": "auto", "action": "Enable the matching Datadog integration on the host."},
        {"when": "Script, PowerShell, WMI or custom template", "result": "review", "action": "Rebuild as a custom Agent check or DogStatsD metric."},
        {"when": "Template has no Datadog equivalent", "result": "unsupported", "action": "Document and decide whether to retire it."},
        {"when": "Migrated when", "result": "match", "action": "The host reports the integration in its Datadog app list."},
    ]},
    {"category": "alerts", "source": "Alert", "target": "Monitor", "rules": [
        {"when": "Enabled alert on Node, Interface or Volume", "result": "auto", "action": "Create a metric or service-check monitor with equivalent thresholds."},
        {"when": "Application, group, custom SWQL, or disabled alert", "result": "review", "action": "Trigger logic needs manual translation (or a decision to drop it)."},
        {"when": "Object type with no Datadog analogue (e.g. Orion server, license)", "result": "unsupported", "action": "Platform-internal alert."},
        {"when": "Migrated when", "result": "match", "action": "A monitor has the same normalized name or a sw_alert_id:<id> tag."},
    ]},
    {"category": "dependencies", "source": "Dependency", "target": "Service relationship", "rules": [
        {"when": "Node-to-node or group dependency", "result": "review", "action": "Model with dependsOn in the Service Catalog, or composite monitors for alert suppression."},
        {"when": "Parent or child cannot be resolved", "result": "unsupported", "action": "Orphaned dependency."},
        {"when": "Migrated when", "result": "match", "action": "A service definition for the child lists the parent in dependsOn."},
    ]},
    {"category": "snmp_devices", "source": "SNMP device", "target": "NDM Device", "rules": [
        {"when": "SNMP v1, v2c or v3", "result": "auto", "action": "Add to NDM with a matching profile (sysObjectID)."},
        {"when": "No sysObjectID recorded", "result": "review", "action": "Confirm a Datadog SNMP profile exists for the device."},
        {"when": "Migrated when", "result": "match", "action": "An NDM device with the same IP or name exists."},
    ]},
]

# Technology keyword -> Datadog integration. First match wins.
SAM_INTEGRATIONS = [
    (("iis", "internet information"), "iis"),
    (("sql server", "mssql"), "sqlserver"),
    (("exchange",), "exchange_server"),
    (("active directory", "domain controller"), "active_directory"),
    (("tomcat",), "tomcat"),
    (("apache",), "apache"),
    (("nginx",), "nginx"),
    (("mysql",), "mysql"),
    (("postgres",), "postgres"),
    (("oracle",), "oracle"),
    (("mongo",), "mongo"),
    (("redis",), "redisdb"),
    (("rabbitmq",), "rabbitmq"),
    (("vmware", "esx", "vcenter"), "vsphere"),
    (("hyper-v", "hyperv"), "hyperv"),
    (("docker",), "docker"),
    (("dhcp",), "windows_service"),
    (("dns",), "dns_check"),
    (("http", "https", "url", "web"), "http_check"),
    (("tcp port", "port monitor"), "tcp_check"),
    (("windows service", "service monitor"), "windows_service"),
    (("process",), "process"),
    (("certificate", "ssl"), "tls"),
    (("ldap",), "openldap"),
    (("smtp", "pop3", "imap"), "tcp_check"),
]
# A node is treated as a Linux server when its sysObjectID is the net-snmp Linux
# agent OID, or its vendor / machine type / OS version contains one of these.
LINUX_SYS_OBJECT_ID = "1.3.6.1.4.1.8072.3.2.10"
LINUX_KEYWORDS = ("linux", "red hat", "rhel", "centos", "rocky", "almalinux", "ubuntu",
                  "debian", "suse", "sles", "oracle linux", "amazon linux", "fedora")

SAM_REVIEW_KEYWORDS = ("script", "powershell", "wmi", "performance counter", "custom", "snmp", "file", "event log", "odbc")
