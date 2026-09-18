# Datadog Migration Assistant

A self-hosted, Docker-based web application that inventories a legacy monitoring
platform, maps every object to its Datadog equivalent, and tracks how much of
the estate has actually been migrated — scan after scan.

Version 1.0 supports **SolarWinds Orion** as the source platform. Zabbix,
Checkmk, New Relic, Nagios XI and PRTG are shown on the start page as planned
sources and are not yet selectable.

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [How it works](#2-how-it-works)
3. [Requirements](#3-requirements)
4. [Quick start](#4-quick-start)
5. [Configuration](#5-configuration)
6. [Access and permissions](#6-access-and-permissions)
7. [Using the application](#7-using-the-application)
8. [What gets collected](#8-what-gets-collected)
9. [Mapping model](#9-mapping-model)
10. [Mapping rules by category](#10-mapping-rules-by-category)
11. [Matching logic: how "migrated" is decided](#11-matching-logic-how-migrated-is-decided)
12. [Scores and progress tracking](#12-scores-and-progress-tracking)
13. [Reports and exports](#13-reports-and-exports)
14. [Demo mode](#14-demo-mode)
15. [REST API reference](#15-rest-api-reference)
16. [Data model and storage](#16-data-model-and-storage)
17. [Security](#17-security)
18. [Deployment guide](#18-deployment-guide)
19. [Backup, restore and upgrades](#19-backup-restore-and-upgrades)
20. [Troubleshooting](#20-troubleshooting)
21. [Performance and limits](#21-performance-and-limits)
22. [Development](#22-development)
23. [Extending the application](#23-extending-the-application)
24. [Project layout](#24-project-layout)
25. [Known limitations and roadmap](#25-known-limitations-and-roadmap)

---

## 1. What it does

A migration from SolarWinds to Datadog is usually tracked in spreadsheets that
go stale the day they are written. This application replaces that with a
repeatable scan:

- **Inventories** nine SolarWinds object categories: nodes, interfaces,
  volumes, groups, custom properties, SAM applications, alerts, dependencies
  and SNMP devices.
- **Reads** the current state of the target Datadog organization: hosts, host
  tags, monitors (including http_check monitors), Synthetic HTTP and browser
  tests, dashboards, Network Device Monitoring (NDM) devices and interfaces,
  and Service Catalog definitions.
- **Maps** each SolarWinds object to its Datadog equivalent and classifies it
  as *automatic*, *needs review* or *unsupported*.
- **Checks** whether an equivalent already exists in Datadog, marking each
  object *migrated* or *pending*.
- **Scores** migration readiness overall and per category.
- **Compares** each scan with the previous one to show what was newly
  migrated, what regressed, and what was added or removed in SolarWinds.
- **Reports**: an overall assessment report, a detailed report and CSV for
  every category, and a full JSON export.

Everything is **read-only**. The application never creates, modifies or
deletes anything in SolarWinds or Datadog.

### Feature summary

| Feature | Where |
|---|---|
| Source platform selection | Start page |
| SolarWinds and Datadog connection configuration with live tests | Connections page |
| Overall migration-readiness dashboard with trend | Overview |
| Inventory for all nine SolarWinds categories, with search and filters | Inventory pages |
| SolarWinds-to-Datadog mapping rules | Mapping rules page |
| Automatic mapping, needs-review and unsupported counts | Overview, inventory, reports |
| Overall migration assessment report | Reports page |
| Separate detailed report for every mapped element | Reports page |
| Progress since the previous scan, per category | Overview, scan history, reports |
| Responsive Datadog-inspired dark interface | Everywhere; works down to phone width |
| Sign-in with local accounts, sessions and API tokens | Login page, Account page |

---

## 2. How it works

```
 ┌──────────────┐   SWIS REST (HTTPS :17774)   ┌──────────────────────────────┐
 │  SolarWinds  │ ◄─────────────────────────── │                              │
 │    Orion     │        SWQL queries          │   Datadog Migration          │
 └──────────────┘                              │   Assistant (container)      │
                                               │                              │
 ┌──────────────┐   Datadog API (HTTPS :443)   │  FastAPI  ─  scan worker     │
 │   Datadog    │ ◄─────────────────────────── │  mapping engine  ─  reports  │
 │ organization │     read-only GET calls      │  SQLite (/data volume)       │
 └──────────────┘                              └──────────────┬───────────────┘
                                                              │ HTTP :8080
                                                        ┌─────▼─────┐
                                                        │  Browser  │
                                                        └───────────┘
```

A scan runs in a background thread and moves through these stages. The
progress bar on the Overview page shows the current stage.

| Progress | Stage |
|---|---|
| 1–55% | Read the eight SolarWinds query groups (SNMP devices are derived from nodes) |
| 58–88% | Read Datadog hosts, tags, monitors, dashboards, NDM devices, services, synthetic tests |
| 92% | Run the mapping engine and compare with the previous completed scan |
| 100% | Store results and summary; scan marked `complete` |

If a single category fails (for example SAM is not installed, or the Datadog
key lacks one permission), that category is recorded as a **scan warning** and
the scan continues. A scan fails outright only when a connection cannot be made
or credentials are rejected.

### Components

| Component | Technology | Responsibility |
|---|---|---|
| API server | FastAPI + Uvicorn | REST API, static UI hosting, scan orchestration |
| SolarWinds connector | httpx | SWQL queries over the SWIS JSON API |
| Datadog connector | httpx | Paginated, rate-limit-aware GET requests |
| Mapping engine | Python | Classification, matching, scoring, scan comparison |
| Report generator | Python | Standalone HTML reports, CSV |
| Storage | SQLite | Projects (encrypted credentials) and scan results |
| UI | Vanilla JavaScript, no build step | Single-page app with hash routing |

---

## 3. Requirements

### Host

- Docker Engine 20.10+ with Docker Compose v2 (`docker compose`).
- 1 vCPU and 512 MB RAM is enough for most environments. Allow 1 GB RAM for
  estates above ~20,000 SolarWinds objects.
- Disk: roughly 1–5 MB per scan for typical environments (results are stored
  as JSON). See [Performance and limits](#21-performance-and-limits).

### Network

| From | To | Port | Purpose |
|---|---|---|---|
| Container | SolarWinds Orion main poller | TCP 17774 (HTTPS) | SWIS API |
| Container | `api.<datadog-site>` | TCP 443 | Datadog API |
| Browser | Container host | TCP 8080 (configurable) | Web UI |
| Browser | `fonts.googleapis.com` / `fonts.gstatic.com` | TCP 443 | Optional web font (IBM Plex Sans) |

The web font is optional. On networks without internet access the UI falls
back to system fonts with no loss of function.

### Supported SolarWinds versions

Any Orion Platform release exposing SWIS v3 over REST (Orion 2016.1 and later,
including the SolarWinds Platform 2022–2025 releases). NPM is required for the
interfaces category and SAM for the applications category; if either module is
missing, that category is skipped with a warning.

### Supported Datadog sites

| Site value | Region |
|---|---|
| `datadoghq.com` | US1 |
| `us3.datadoghq.com` | US3 |
| `us5.datadoghq.com` | US5 |
| `datadoghq.eu` | EU1 |
| `ap1.datadoghq.com` | AP1 |
| `ap2.datadoghq.com` | AP2 |
| `ddog-gov.com` | US1-FED (GovCloud) |

---

## 4. Quick start

```bash
# 1. Unpack and enter the project
unzip datadog-migration-assistant.zip
cd datadog-migration-assistant

# 2. Create the environment file and set a real secret
cp .env.example .env
sed -i "s|^DMA_SECRET_KEY=.*|DMA_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n')|" .env

# 3. Build and start
docker compose up -d --build

# 4. Confirm it is healthy
docker compose ps
curl -s http://localhost:8080/api/health     # {"status":"ok"}
```

Open `http://<docker-host>:8080`. The first visit asks you to create a sign-in
account (or set `DMA_ADMIN_USER` / `DMA_ADMIN_PASSWORD` beforehand). Then
choose **SolarWinds Orion** and follow
[Using the application](#7-using-the-application).

To try it without real systems, tick **Demo data** on both connection panels.
See [Demo mode](#14-demo-mode).

### Stopping and removing

```bash
docker compose down          # stop; data is kept in the dma-data volume
docker compose down -v       # stop AND delete all projects and scans
```

---

## 5. Configuration

All configuration is through environment variables in `.env`. Connection
details for SolarWinds and Datadog are entered in the UI, not in `.env`.

| Variable | Default | Description |
|---|---|---|
| `DMA_SECRET_KEY` | *(generated)* | Secret used to encrypt stored credentials. If unset, a random key is generated at `/data/.dma_secret` on first start. **Set it explicitly** so credentials remain readable after restoring the data volume on another host. |
| `DMA_DB_PATH` | `/data/dma.sqlite3` | SQLite database location. The generated secret file is stored in the same directory. |
| `DMA_LOG_LEVEL` | `INFO` | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `DMA_ADMIN_USER` / `DMA_ADMIN_PASSWORD` | *(unset)* | Optional: create the first sign-in account on first start. If unset, the first visit shows a setup page instead. Only used while no account exists. |
| `DMA_SESSION_HOURS` | `12` | How long a sign-in lasts before it expires. |
| `DMA_SAM_HTTP_COMPONENT_TYPES` | *(unset)* | Optional comma-separated SAM component type IDs that are HTTP/HTTPS monitors in your Orion (for example `6,14`). Confirm the IDs first; see [Verifying HTTP(S) components in SWQL](#verifying-https-components-in-swql). |
| `DMA_SAM_PROCESS_COMPONENT_TYPES` | *(unset)* | Same idea for SAM process-monitor component types. |
| `HTTPS_PROXY` / `NO_PROXY` | *(unset)* | Optional outbound proxy; see [Outbound HTTP proxy](#186-outbound-http-proxy). |

### Changing the port

Edit the left side of the port mapping in `docker-compose.yml`:

```yaml
ports:
  - "9090:8080"     # UI now on host port 9090
```

### Binding to localhost only

When a reverse proxy runs on the same host, keep the app off the network:

```yaml
ports:
  - "127.0.0.1:8080:8080"
```

---

## 6. Access and permissions

### SolarWinds account

Create a dedicated Orion web console account for the assistant.

- **Account type:** Orion individual account, or a Windows/AD account that can
  log in to the Orion web console.
- **Rights needed:** read access only. No admin, node management, alert
  management or report management rights are required.
- **Account limitations:** Orion applies the account's limitations to SWIS
  queries. If the account is limited to certain groups or sites, the inventory
  will only contain those objects. Use an unlimited account for a full
  assessment, or a limited one to scope the migration deliberately.
- **Port:** SWIS listens on TCP 17774 on the main polling engine. Confirm from
  the Docker host with:

  ```bash
  curl -k -u 'user:pass' \
    -H 'Content-Type: application/json' \
    -d '{"query":"SELECT TOP 1 NodeID FROM Orion.Nodes"}' \
    https://orion.example.com:17774/SolarWinds/InformationService/v3/Json/Query
  ```

### Datadog keys

You need both keys:

- **API key** — Organization Settings → API Keys.
- **Application key** — Organization Settings → Application Keys (or a
  service account's application key, recommended so the key is not tied to a
  person).

If you scope the Application key, grant read access to:

| Area | Permission (scope) | Used for |
|---|---|---|
| Hosts | `hosts_read` | Host list, aliases, integrations, host tags |
| Monitors | `monitors_read` | Monitor names and tags |
| Dashboards | `dashboards_read` | Dashboard titles |
| Network Device Monitoring | NDM read permission | NDM devices and interfaces |
| Service Catalog | `apm_service_catalog_read` | Service definitions and `dependsOn` |
| Synthetic Monitoring | `synthetics_read` | Synthetic HTTP and browser tests, matched to SAM HTTP(S) monitors |

A missing permission does not fail the scan; the affected Datadog category is
skipped and shown as a warning, and the matching SolarWinds objects will
appear as *pending*.

---

## 7. Using the application

### 7.1 Signing in

The first time the application starts with no accounts, it shows a **setup
page** asking for a username and password (at least 10 characters). After
that, every visit begins at the **login page**.

- Sessions last `DMA_SESSION_HOURS` (12 by default) and are stored server-side;
  signing out revokes them immediately.
- After 10 failed attempts for the same username and IP address within 15
  minutes, further attempts are refused for the rest of that window.
- The **Account** page (your username in the top bar) changes your password
  and manages who else can sign in. Changing a password signs out every
  session for that account.
- There is no password reset link. If you are locked out, see
  [Resetting an account](#resetting-an-account).

All accounts have the same rights: anyone who can sign in can see and change
every migration.

### 7.2 Start page

Lists the supported source platforms and all existing migrations. Each
migration row shows its source, destination, time of the last scan and current
readiness. Select **SolarWinds Orion** to create a new migration.

### 7.3 Connect SolarWinds and Datadog

| Field | Notes |
|---|---|
| Migration name | Free text, shown throughout the UI and on reports. |
| Orion server | Hostname or IP of the main polling engine. `https://` prefixes and trailing slashes are removed automatically. |
| SWIS port | Default `17774`. |
| Username / Password | Orion web console credentials. |
| Verify the server's TLS certificate | Off by default because most Orion servers use a self-signed certificate. Turn on when Orion has a trusted certificate. |
| Datadog site | Region for your organization. |
| API key / Application key | See [Datadog keys](#datadog-keys). |
| Demo data | Replaces that side of the connection with generated data. |

Use **Test SolarWinds** and **Test Datadog** before saving:

- SolarWinds success shows the node count and installed modules (for example
  `NPM 2024.2, SAM 2024.2`).
- Datadog success shows the region and number of reporting hosts.
- Failures show the exact reason (unreachable host, rejected credentials,
  missing permission).

**Save and run first scan** stores the migration and immediately starts the
first scan.

### 7.4 Overview

The home page of a migration.

- **Migrated to Datadog** — overall readiness percentage, the number of
  in-scope objects found in Datadog, how many were newly migrated since the
  previous scan, and a readiness trend line across all completed scans.
- **Counts** — total SolarWinds objects, already in Datadog, still to migrate,
  automatic mapping, needs review, unsupported.
- **Datadog today** — counts of hosts, network devices, monitors, synthetic
  tests and dashboards seen in the latest scan.
- **Category rows** — one row per SolarWinds category showing:
  - the SolarWinds count and object type,
  - a colored bar with the automatic / needs review / unsupported split,
  - a thin green bar with migration progress for in-scope objects,
  - the Datadog target type and how many were found,
  - the category readiness, pending count and change since the last scan.

  Select a row to open that category's inventory.
- **Run new scan** — starts a scan. Only one scan per migration can run at a
  time. The page shows live progress and refreshes when the scan finishes.
- **Open assessment report** — opens the overall report for the latest scan.
- **Scan warnings** — categories that were skipped and why.

### 7.5 Inventory pages

One page per category, reachable from the sidebar. Each shows:

- counts for the category,
- a searchable, filterable table:
  - **Search** matches any text in the object, including target, notes and
    details,
  - **Mapping** filter: automatic, needs review, unsupported,
  - **Status** filter: migrated, pending,
- per-object columns: SolarWinds object and note, mapping, Datadog target,
  status, what was found in Datadog, and up to five category-specific detail
  columns,
- paging in blocks of 100,
- **Open report** and **Download CSV** for the category.

Status values:

| Status | Meaning |
|---|---|
| Migrated | An equivalent was found in Datadog during this scan. |
| Pending | In scope, but no equivalent found yet. |
| Out of scope | The object is classified *unsupported*. |

### 7.6 Mapping rules

Shows the SolarWinds-to-Datadog mapping table and, for each category, the
conditions that produce *automatic*, *needs review* and *unsupported*, and what
counts as *migrated*. This page is generated from `app/mapping/rules.py`, the
same file the mapping engine reads, so it always reflects actual behaviour.

### 7.7 Reports

Choose any completed scan, then open or download:

- the overall migration assessment (HTML),
- the full scan export (JSON),
- a detailed report (HTML) and CSV for each of the nine categories.

See [Reports and exports](#13-reports-and-exports).

### 7.8 Scan history

- A table of readiness per category for every completed scan (shown once two
  or more scans exist).
- Every scan with status, start time, object count, migrated count, readiness
  and newly migrated count.
- Failed scans show their error message.
- Links to each scan's reports, and **Delete** for scans that are not running.

### 7.9 Connections

Edit the migration name and both connections, or delete the migration.
Password and key fields show *Saved. Leave blank to keep*: leave them empty to
keep stored secrets, or type a new value to replace them. **Delete migration**
removes the migration and all its scans permanently.

### 7.10 Recommended workflow

1. Create the migration and run the first scan. This is your baseline.
2. Read the overall assessment report and export the *needs review* items per
   category for decisions.
3. Apply the [matching conventions](#115-conventions-that-make-matching-exact)
   as you build things in Datadog.
4. Migrate in waves (for example by site or group).
5. Run a new scan after each wave. Check *newly migrated* and *regressed*
   counts, and share the updated assessment report.
6. Repeat until readiness reaches your cutover threshold.

---

## 8. What gets collected

### 8.1 SolarWinds (SWQL)

All queries are sent as `POST /SolarWinds/InformationService/v3/Json/Query`.

| Category | Entities queried | Fields |
|---|---|---|
| Nodes | `Orion.Nodes` | NodeID, Caption, IPAddress, DNS, SysName, Vendor, MachineType, ObjectSubType (polling method), StatusDescription, SNMPVersion, SysObjectID, IOSVersion, Unmanaged |
| Interfaces | `Orion.NPM.Interfaces` | InterfaceID, NodeID, Node.Caption, Name, Caption, TypeDescription, Speed, Status, InterfaceIndex |
| Volumes | `Orion.Volumes` | VolumeID, NodeID, Node.Caption, Caption, VolumeType, VolumeSize, VolumePercentUsed |
| Groups | `Orion.Container`, `Orion.ContainerMembers`, `Orion.ContainerMemberDefinition` | ContainerID, Name, Description, Status; members (name, entity type, ID); definitions (a definition starting with `filter:` marks a dynamic group) |
| Custom properties | `Orion.CustomProperty`, then `Orion.NodesCustomProperties`, `Orion.NPM.InterfacesCustomProperties`, `Orion.VolumesCustomProperties` | Table, Field, DataType, Description; value count, distinct value count, up to 8 most common values |
| Applications | `Orion.APM.Application` joined to `Orion.APM.ApplicationTemplate`; `Orion.APM.Component`; `Orion.APM.ComponentSetting` | ApplicationID, Name, NodeID, Node.Caption, template name; component ID, name, type; component settings `Url` (HTTP/HTTPS monitors) and `ProcessName` / `ProcessNameFilter` / `ProcessCommandLine` (process monitors) |
| Alerts | `Orion.AlertConfigurations` | AlertID, Name, Description, Enabled, Severity, ObjectType, Frequency |
| Dependencies | `Orion.Dependencies` | DependencyId, Name, ParentUri, ChildUri, AutoManaged. URIs are resolved to node or group names. |
| SNMP devices | Derived from nodes | Nodes whose polling method is `SNMP` |

Notes:

- Group member and definition queries are optional. If they fail, groups are
  still listed (member count 0, not dynamic).
- Component and component-URL queries are optional. If they fail,
  applications are still listed without components or URLs, and the failure
  is shown as a **scan warning** (HTTP(S) monitors then fall back to name
  matching).
- `Key` is a reserved word in SWQL, so the settings query uses
  `[Key] IN ('Url', 'ProcessName', …)`.
- Custom property values are only read for field names made of letters,
  digits and underscores. See [Known limitations](#25-known-limitations-and-roadmap).
- Custom property tables are recognized under both naming forms Orion uses
  (`Interfaces` / `InterfacesCustomProperties`, `Volumes` /
  `VolumesCustomProperties`).
- Only up to 500 members per group are stored in the results.

### 8.2 Datadog

| Data | Endpoint | Paging |
|---|---|---|
| Key validation | `GET /api/v1/validate` | — |
| Hosts (name, aliases, apps, sources, tags, platform) | `GET /api/v1/hosts` | 1,000 per page |
| Host tags | `GET /api/v1/tags/hosts` | — |
| Monitors (ID, name, type, query, tags) | `GET /api/v1/monitor` | 1,000 per page |
| Dashboards (ID, title) | `GET /api/v1/dashboard` | — |
| NDM devices (ID, name, IP, vendor, model, status, tags) | `GET /api/v2/ndm/devices` | 500 per page |
| NDM interfaces per device | `GET /api/v2/ndm/interfaces?device_id=…` | first 1,000 devices |
| Service definitions (`dd-service`, `dependsOn`) | `GET /api/v2/services/definitions` | 100 per page |
| Synthetic tests (ID, name, type, target URL, tags); API HTTP and browser tests only | `GET /api/v1/synthetics/tests` | 100 per page |

HTTP 429 responses are retried up to five times, waiting for the number of
seconds in `X-RateLimit-Reset` (between 1 and 60 seconds). The NDM and
Service Catalog endpoints tolerate 404 (feature not enabled) and return an
empty list.

http_check monitors are not a separate call: they are recognized among the
monitors by queries that use `http.can_connect`, `http.response_time` or
`http.ssl.*`, and their target URLs are read from `url:` tags in the query.

### 8.3 What is not collected

- No metric, event, log or performance data.
- No SolarWinds alert trigger/reset conditions or actions (only alert
  definition metadata).
- No passwords, SNMP community strings or credentials from either system.

---

## 9. Mapping model

| SolarWinds | Datadog |
|---|---|
| Node | Host / Device |
| Custom Property | Tag |
| Group | Tag / Dashboard |
| Alert | Monitor |
| SAM Monitor (application) | Integration / Check |
| Interface | NDM Interface |
| Dependency | Service relationship |
| Volume | Host disk check |
| SNMP device | NDM Device |

Every object receives **two independent results**.

**Mapping classification** — how the object translates:

| Value | UI label | Meaning |
|---|---|---|
| `auto` | Automatic | A direct Datadog equivalent exists and can be set up by standard means. |
| `review` | Needs review | A person must decide how (or whether) to rebuild it. |
| `unsupported` | Unsupported | Out of scope: no equivalent, or nothing to migrate. |

**Migration status** — whether it already exists in Datadog:

| Value | Meaning |
|---|---|
| `migrated: true` | An equivalent was found during this scan. |
| `migrated: false` | Pending (or out of scope if unsupported). |

Unsupported objects are never counted as migrated, even if a match exists.

Each object also carries:

- a **suggested Datadog target** (for example `sqlserver`,
  `business_unit:<value>`, `[SW] Node is down`),
- a **note** explaining the classification,
- the **Datadog match** that was found, if any (for example
  `host:web01.corp`, `monitor #1234: Node is down`),
- category-specific **details** (IP, polling method, template, member count
  and so on).

---

## 10. Mapping rules by category

### 10.1 Nodes → Host / Device

| Condition | Result | Suggested target |
|---|---|---|
| Unmanaged, or polling method is `External` or empty | Unsupported | — |
| Polled by `Agent` or `WMI` | Automatic | Datadog Agent host |
| Polled by `SNMP` **and is a Linux server** | Automatic | Datadog Agent host: install the Datadog Agent; the node becomes a Host |
| Polled by `SNMP` (network devices and other non-Linux systems) | Automatic | NDM device |
| Anything else (ICMP-only) | Needs review | Synthetic ICMP test |

Rules are evaluated top to bottom; the first match wins. Linux servers are
often polled by SNMP (net-snmp) in SolarWinds, but in Datadog they belong on
the Agent, not in NDM. See [Windows and Linux server detection](#116-windows-and-linux-server-detection).

**Migrated when:** the node's Caption, DNS name or SysName matches a Datadog
host name or alias, **or** its IP address or name matches an NDM device.

Details: IP, polling method, Linux server (yes/no), vendor, machine type,
status.

### 10.2 Interfaces → NDM Interface

| Condition | Result | Suggested target |
|---|---|---|
| Parent node polled by Agent or SNMP **and is a Linux server** | Needs review | `system.net.*` agent metrics: covered by agent metrics, not NDM interfaces |
| Parent node polled by SNMP (not a Linux server) | Automatic | NDM interface |
| Parent node polled by Agent or WMI (not a Linux server) | Needs review | `system.net.*` agent metrics |
| Parent node ICMP-only, external or missing | Unsupported | — |

Rules are evaluated top to bottom; the first match wins.

**Migrated when:**

- SNMP network device parent: the parent's NDM device reports an interface
  whose name equals the SolarWinds interface Name or Caption
  (case-insensitive).
- Linux server or Agent/WMI parent: the parent node exists as a Datadog host.

Details: node, polling method, Linux server (yes/no), type, speed, interface
index.

### 10.3 Volumes → Host disk check

| Condition (VolumeType contains…) | Result | Suggested target |
|---|---|---|
| `fixed` or `mount` | Automatic | `system.disk.*` |
| `ram`, `virtual`, `memory` or `other` | Needs review | `system.mem.*` / `system.swap.*` |
| Anything else (removable, network, compact disc) | Unsupported | — |

**Migrated when:** the parent node exists as a Datadog host.

Details: node, type, size (GB).

### 10.4 Groups → Tag / Dashboard

| Condition | Result | Suggested target |
|---|---|---|
| No members | Unsupported | `sw_group:<name>` |
| Dynamic group (definition starts with `filter:`) | Needs review | `sw_group:<name>` |
| Static group with members | Automatic | `sw_group:<name>` |

**Migrated when:** a host tag `sw_group:<normalized group name>` exists, **or**
a dashboard title contains the group name (case-insensitive).

Details: members, dynamic, description.

### 10.5 Custom properties → Tag

| Condition | Result | Suggested target |
|---|---|---|
| No values, or not on Nodes, Interfaces or Volumes | Unsupported | `<key>:<value>` |
| Data type contains `date`, or more than 250 distinct values | Needs review | `<key>:<value>` |
| Otherwise (text, number, yes/no with values) | Automatic | `<key>:<value>` |

**Migrated when:** a Datadog host tag with the normalized key exists (for
example `Business Unit` → `business_unit:*`).

Details: table, data type, values, distinct values, sample values.

### 10.6 Applications (SAM) → Integration / Check

The application's template name and application name are combined and
checked against a keyword list. Keywords match **whole words only** (so
`custom` does not match "Customer" and `web` does not match "webhook").
**The first matching keyword wins**, so order matters. A match on
`http_check` identifies an **HTTP or HTTPS monitor**, which follows its own
rule (see
[HTTP and HTTPS monitor templates](#http-and-https-monitor-templates) below).

| Keywords | Datadog integration |
|---|---|
| iis, internet information | `iis` |
| sql server, mssql | `sqlserver` |
| exchange | `exchange_server` |
| active directory, domain controller | `active_directory` |
| tomcat | `tomcat` |
| apache | `apache` |
| nginx | `nginx` |
| mysql | `mysql` |
| postgres | `postgres` |
| oracle | `oracle` |
| mongo | `mongo` |
| redis | `redisdb` |
| rabbitmq | `rabbitmq` |
| vmware, esx, vcenter | `vsphere` |
| hyper-v, hyperv | `hyperv` |
| docker | `docker` |
| dhcp | `windows_service` |
| dns | `dns_check` |
| http, https, url, web | `http_check` |
| tcp port, port monitor | `tcp_check` |
| windows service, service monitor | `windows_service` |
| process | `process` |
| certificate, ssl | `tls` |
| ldap | `openldap` |
| smtp, pop3, imap | `tcp_check` |

| Condition | Result | Suggested target |
|---|---|---|
| HTTP or HTTPS monitor (see detection below) | Automatic | Synthetic HTTP test or http_check monitor; no Datadog host required |
| Process monitor (see [Process monitors](#process-monitors)) | Automatic | Agent process check (process.d / Live Processes) plus a process monitor |
| Another keyword above matches | Automatic | The integration |
| No match, but contains script, powershell, wmi, performance counter, custom, snmp, file, event log or odbc | Needs review | Custom Agent check |
| No match at all | Unsupported | — |

**Migrated when (non-HTTP templates):** the application's node exists as a
Datadog host and that host's integration list contains the integration name
(or the name without a `_check` suffix).

#### HTTP and HTTPS monitor templates

**Detection.** An application is treated as an HTTP(S) monitor when:

1. its template or application name contains the word HTTP, HTTPS, URL or
   web, **or**
2. its name matches no other known technology, and **any of its components**:
   - has a `Url` setting starting with `http://` or `https://`,
   - has "HTTP" or "HTTPS" as a word in its name, or
   - has a component type listed in `DMA_SAM_HTTP_COMPONENT_TYPES`.

This means custom applications such as "Customer Portal" or "Payments
Gateway" that contain HTTPS Monitor components are detected, even though
their names say nothing about HTTP. An application whose name matches another
technology (for example "Microsoft IIS" with an HTTP component inside) keeps
that technology's integration rule.

The **Detected by** column on the Applications page and in the CSV
(`detected_by`) shows which signal was used: `template/application name`,
`component URL`, `component type` or `component name`.

An HTTP(S) monitor watches a URL, not a server, so **it does not need to map to
a node**. The node it is assigned to in SolarWinds is shown for reference only;
it does not have to exist in Datadog.

The monitored URLs are read from each component's `Url` setting. The monitor
counts as **migrated** when Datadog has either:

- a **Synthetic HTTP test** (API test) or a **browser test**, or
- a **normal http_check monitor** (a monitor on `http.can_connect`,
  `http.response_time` or `http.ssl.*`),

that matches, checked in this order (the level that matched is recorded as the
**Match quality** column, so you can tell an exact hit from a guess):

1. **Same URL.** Compared after normalization: scheme and hostname
   lowercased, default ports (80, 443) and trailing slashes dropped, query
   strings ignored. `HTTPS://Portal.example.com:443/health/` equals
   `https://portal.example.com/health`.
2. **Same hostname.** Any test or monitor on the same host, even with a
   different path. The match is labelled *(same hostname)* in the
   "Found in Datadog" column so you can verify it.
3. **Similar name.** When no URL matches (or SolarWinds returned no URL), a
   test or monitor whose name shares at least 80% of its words with the SAM
   application name. Labelled *(similar name)*.

| Match quality | Meaning |
|---|---|
| `exact URL` | Same URL after normalization. The strongest result. |
| `same hostname` | A test or monitor on that host, but a different path. Verify it. |
| `similar name` | No URL available or no URL matched; names lined up. Verify it. |
| *(blank)* | Nothing found: still to migrate. |

Examples of what is found:

| Datadog object | Shown as |
|---|---|
| Synthetic API test `abc-123-xyz` "Portal up" on `https://portal.example.com/health` | `synthetic http test abc-123-xyz: Portal up` |
| Monitor 42 with query `"http.can_connect".over("instance:status","url:http://status.example.com/")…` | `http_check monitor #42: Status page` |

Tip: an http_check monitor that only filters by `instance:` has no URL in its
query and can only match by name. Include `url:<address>` in the monitor
query, or name the monitor after the SAM application, to get an exact match.

Details: node, template, URLs, detected by and match quality (HTTP(S)
monitors), component count, first six component names. Unsupported applications list
their template and first component names in the note, to make
misclassification easy to spot.

#### Process monitors

SAM process monitors (Windows, Linux or Unix) map to the **Agent process
check** (`process.d`, surfaced as Live Processes) with a **process monitor**
for alerting. Unlike HTTP(S) monitors, these do run on a host.

**Detection.** An application is treated as a process monitor when:

1. its template or application name contains the word "process", **or**
2. its name matches no other known technology, and any of its components:
   - has a `ProcessName`, `ProcessNameFilter` or `ProcessCommandLine`
     setting,
   - has "process" or "processes" as a word in its name, or
   - has a component type listed in `DMA_SAM_PROCESS_COMPONENT_TYPES`.

HTTP(S) detection is checked first, so an application containing both an HTTP
component and a process component follows the HTTP rule.

**Migrated when**, in this order:

| Match quality | What matched |
|---|---|
| `process name` | A Datadog process monitor (type `process alert`, or a query using `processes(…)` or `process.up`) whose name or query mentions the monitored process. A `.exe`, `.sh` or `.bat` suffix is ignored, so `w3wp.exe` matches `w3wp`. |
| `host process check` | The application's node is a Datadog host that reports the `process` integration. |
| `similar name` | A process monitor whose name shares at least 80% of its words with the SAM application name. Used when no process name was read. |
| *(blank)* | Nothing found: still to migrate. |

Details: node, template, processes, detected by, match quality, component
count, component names.

Verify what will be read with:

```sql
SELECT a.Name AS Application, c.Name AS Component, c.ComponentType,
       s.[Key] AS SettingKey, s.Value
FROM Orion.APM.Component c
JOIN Orion.APM.Application a ON a.ApplicationID = c.ApplicationID
JOIN Orion.APM.ComponentSetting s ON s.ComponentID = c.ComponentID
WHERE s.[Key] IN ('Url', 'ProcessName', 'ProcessNameFilter', 'ProcessCommandLine')
ORDER BY a.Name
```

#### Verifying HTTP(S) components in SWQL

Run this in SWQL Studio to see what the assistant will read. Every HTTP(S)
monitor component should appear with its URL:

```sql
SELECT a.Name AS Application, t.Name AS Template, c.ComponentID,
       c.Name AS Component, c.ComponentType, s.Value AS Url
FROM Orion.APM.Component c
JOIN Orion.APM.Application a ON a.ApplicationID = c.ApplicationID
LEFT JOIN Orion.APM.ApplicationTemplate t ON t.ApplicationTemplateID = a.ApplicationTemplateID
JOIN Orion.APM.ComponentSetting s ON s.ComponentID = c.ComponentID
WHERE s.[Key] = 'Url'
ORDER BY a.Name
```

- If this returns your components, URL-based detection and matching will work.
- If it returns nothing, check the setting key name for your SAM version with
  `SELECT DISTINCT s.[Key] FROM Orion.APM.ComponentSetting s` and let the
  maintainer know.
- The `ComponentType` values shown are the IDs to put in
  `DMA_SAM_HTTP_COMPONENT_TYPES` if you want type-based detection as well.

### 10.7 Alerts → Monitor

| Condition | Result | Suggested target |
|---|---|---|
| Alert is disabled | Needs review | `[SW] <alert name>` |
| Enabled, object type Node, Interface or Volume | Automatic | `[SW] <alert name>` |
| Enabled, object type contains orion, license, engine, poller or database | Unsupported | — |
| Any other enabled alert (application, component, group, custom SWQL, hardware…) | Needs review | `[SW] <alert name>` |

**Migrated when** (checked in order):

1. a monitor is tagged `sw_alert_id:<AlertID>`,
2. a monitor's name equals the alert name after normalization (lowercase,
   letters and digits only),
3. a monitor's name shares at least 80% of its words with the alert name
   (Jaccard similarity on words of two or more characters).

Details: object type, enabled, severity.

### 10.8 Dependencies → Service relationship

| Condition | Result | Suggested target |
|---|---|---|
| Parent or child cannot be resolved to an entity | Unsupported | — |
| Otherwise | Needs review | `<child> dependsOn <parent>` |

Datadog has no direct equivalent of SolarWinds dependency-based alert
suppression, so resolvable dependencies always need a decision: model them
with `dependsOn` in the Service Catalog, or use composite monitors or
downtimes.

**Migrated when:** a service definition named after the child (short host
name) lists the parent (short host name) in `dependsOn`.

Details: parent, child.

### 10.9 SNMP devices → NDM Device

The category lists every node polled by SNMP. **Only nodes that are not
Windows or Linux servers are mapped to NDM.**

| Condition | Result | Suggested target |
|---|---|---|
| Node is a Windows or Linux server | Unsupported | — (migrated as an Agent host; see Nodes) |
| SNMP v1, v2c or v3, sysObjectID recorded, not a Windows or Linux server | Automatic | NDM device |
| Not a Windows or Linux server, but no sysObjectID or no recognized SNMP version | Needs review | NDM device (confirm the version and a profile) |

Rules are evaluated top to bottom; the first match wins. SolarWinds reports
the SNMP version as 1, 2 or 3; these are shown as `v1`, `v2c` and `v3`.

Windows and Linux servers stay visible in this category so nothing
disappears from the inventory, but as *unsupported* they are excluded from its
readiness score, and they are not counted as migrated even if someone added
them to NDM. See [Windows and Linux server detection](#116-windows-and-linux-server-detection).

**Migrated when:** an NDM device with the same IP address or name exists.

Details: IP, SNMP version, server OS (Windows, Linux or blank), vendor, model,
sysObjectID.

---

## 11. Matching logic: how "migrated" is decided

### 11.1 Host name normalization

Used for nodes, volumes, interfaces on agent hosts, applications and
dependencies.

- Lowercased and trimmed.
- IP addresses are kept whole.
- Otherwise, only the part before the first dot is used:
  `WEB01.corp.example.com` → `web01`.

Datadog host names **and** aliases are indexed this way. A SolarWinds node is
checked by Caption, then DNS, then SysName.

> **Watch for short-name collisions.** Two hosts named `app01.site-a` and
> `app01.site-b` both normalize to `app01`. If your naming relies on the
> domain, see [Change host name matching](#233-change-host-name-matching).

### 11.2 NDM device matching

By exact IP address first, then by normalized device name.

### 11.3 Tag key normalization

Applied to custom property names and group names:

1. lowercase and trim,
2. every run of characters other than `a–z`, `0–9`, `_`, `.`, `/`, `-`
   becomes `_`,
3. leading and trailing `_` removed,
4. prefixed with `sw_` if it does not start with a letter.

| SolarWinds | Datadog tag key / value |
|---|---|
| `Business Unit` | `business_unit` |
| `Site-Code` | `site-code` |
| `2nd Owner` | `sw_2nd_owner` |
| Group `Boston Datacenter` | `sw_group:boston_datacenter` |

### 11.4 Monitor name normalization

`Node is DOWN!` and `node is down` both become `nodeisdown`.

### 11.5 Conventions that make matching exact

Name-based matching is a fallback. Adopting these conventions while building
Datadog makes status reliable:

| Convention | Example |
|---|---|
| Tag monitors with the SolarWinds alert ID | `sw_alert_id:42` |
| Tag hosts with group membership | `sw_group:boston_datacenter` |
| Name tags after the custom property | `environment:prod`, `business_unit:payments` |
| Keep host names or aliases consistent with Orion captions | `web01` |
| Declare dependencies in the Service Catalog | `dependsOn: [core-sw-01]` |
| Point Synthetic tests and http_check monitors at the same URL SAM uses | `https://portal.example.com/health` |

The SolarWinds alert ID is the `source_id` column in the Alerts CSV.

### 11.6 Windows and Linux server detection

SolarWinds has no "server operating system" field, so the engine infers it.

**Windows server** (used by the SNMP device rule). A node is treated as a
Windows server when **either**:

- its sysObjectID is under the Microsoft Windows OID `1.3.6.1.4.1.311.1.1.3`
  (for example `…311.1.1.3.1.2` for Windows Server, `…311.1.1.3.1.3` for a
  domain controller), with or without a leading dot, **or**
- its Vendor, MachineType or OS version (IOSVersion) contains `windows`
  (ignoring case).

Extend `WINDOWS_KEYWORDS` in `app/mapping/rules.py` if your Orion uses other
naming.

**Linux server** (used by the node, interface and SNMP device rules). A node
is treated as a Linux server when **either**:

- its sysObjectID is the net-snmp Linux agent OID `1.3.6.1.4.1.8072.3.2.10`
  (with or without a leading dot), **or**
- its Vendor, MachineType or OS version (IOSVersion) contains, ignoring case,
  one of: `linux`, `red hat`, `rhel`, `centos`, `rocky`, `almalinux`,
  `ubuntu`, `debian`, `suse`, `sles`, `oracle linux`, `amazon linux`,
  `fedora`.

A vendor of `net-snmp` on its own is **not** enough, because net-snmp also
runs on Solaris, AIX and other systems (their sysObjectIDs differ, for example
`1.3.6.1.4.1.8072.3.2.3`).

The Linux result appears as the **Linux server** column on the Nodes and
Interfaces inventory pages and in their CSV exports (`linux_server`). The SNMP
devices page and CSV show a **Server OS** column (`server_os`) with `Windows`,
`Linux` or blank.

To recognize more distributions or naming used in your Orion, add keywords to
`LINUX_KEYWORDS` in `app/mapping/rules.py`.

---

## 12. Scores and progress tracking

### 12.1 Counts

For each category and overall:

| Field | Definition |
|---|---|
| `total` | All objects discovered |
| `auto`, `review`, `unsupported` | Objects per mapping classification |
| `in_scope` | `total − unsupported` |
| `migrated` | In-scope objects found in Datadog |
| `pending` | In-scope objects not found |
| `readiness` | `migrated ÷ in_scope × 100`, one decimal. Reported as 100 when nothing is in scope (the UI shows "—"). |
| `auto_rate` | `auto ÷ total × 100` |

Overall figures are computed across all objects in all nine categories, so
large categories (typically interfaces) carry more weight than small ones. Use
the per-category readiness to judge each area on its own.

### 12.2 Scan-to-scan comparison

Each completed scan is compared with the **previous completed scan of the same
migration**, matching objects by SolarWinds ID within each category.

| Field | Meaning |
|---|---|
| `newly_migrated` | Present in both scans; pending before, migrated now |
| `regressed` | Present in both scans; migrated before, not found now |
| `added` | New in SolarWinds since the previous scan |
| `removed` | No longer in SolarWinds |

Up to 50 names are stored for newly migrated and regressed objects per
category and shown in category reports.

A regression usually means a Datadog host stopped reporting, a tag or monitor
was renamed, or the key lost a permission (check scan warnings).

### 12.3 Trend

The Overview trend line and the Scan history table use every completed scan.
Deleting a scan removes it from the trend. The next scan compares against
whatever completed scan is most recent before it.

---

## 13. Reports and exports

All reports are generated on demand from stored scan results, so they can be
produced for any past scan.

### 13.1 Overall migration assessment (HTML)

- Migration name, source, destination, scan number and completion time (UTC).
- Headline figures: readiness, objects discovered, already in Datadog, still
  to migrate, automatic, needs review, unsupported, automatic mapping rate.
- By-category table: mapping, total, automatic, review, unsupported, migrated,
  pending, readiness, change since last scan and a mapping split bar.
- **Where to focus next:** the three in-scope categories with the lowest
  readiness and their pending and review counts.
- Scan warnings.
- Definition of readiness.

### 13.2 Category reports (HTML), one per mapped element

- Category headline figures.
- **Since the previous scan:** newly migrated, regressed, added and removed
  counts, plus up to 20 newly migrated names.
- The mapping rules for that category.
- Every object, sorted pending first, with mapping, Datadog target, status,
  Datadog match, note and up to seven detail columns.

### 13.3 Printing and sharing

Reports are self-contained single HTML files with inline styles and no
scripts. They switch to a light theme when printed; use the browser's
**Print → Save as PDF** to produce a PDF.

### 13.4 CSV (per category)

Columns: `source_id`, `source_name`, `mapping`, `target_type`, `target`,
`migrated` (`yes`/`no`), `datadog_match`, `notes`, then one column per detail
field, alphabetically.

Useful for filtering *needs review* items into a work tracker.

### 13.5 JSON (full scan)

```json
{
  "project": { "name": "...", "source_host": "...", "datadog_site": "..." },
  "scan": { "id": 7, "finished_at": "2026-09-15T21:16:44+00:00" },
  "summary": {
    "scores": { "overall": { "...": "..." }, "categories": { "nodes": { "...": "..." } } },
    "delta": { "nodes": { "newly_migrated": 6, "regressed": 0, "added": 0, "removed": 0 } },
    "previous_scan": 6
  },
  "warnings": ["..."],
  "results": { "nodes": [ { "...": "mapped item" } ] }
}
```

Suitable for feeding other tools (Power BI, a CMDB import, custom scripts).

---

## 14. Demo mode

Tick **Demo data** on the SolarWinds panel, the Datadog panel, or both.

- The generated SolarWinds estate has about 600 objects: three sites with
  Windows and Linux servers (Agent, WMI, SNMP-polled Linux servers and one
  ICMP-only node), Cisco,
  Juniper, Palo Alto and APC network devices, an external node carrying
  HTTP(S) monitors (plus HTTPS monitors on each site's web server), seven groups
  (including a dynamic and an empty one), eleven custom properties, SAM
  applications, sixteen alerts and a set of dependencies, including one
  orphaned dependency.
- The generated Datadog organization contains a growing share of that estate,
  including Synthetic HTTP tests, http_check monitors and process monitors for
  some of the HTTP(S) and process monitors.
  The share is `18% + 17% × (number of completed scans)`, capped at 96%, so
  every new scan shows progress.
- The data is deterministic: the same scan number always produces the same
  result.
- If *either* side is set to demo, that side uses demo data. Mixing a real
  SolarWinds with a demo Datadog (or the reverse) works, but matching results
  will be meaningless because the names do not correspond.

Use demo mode to evaluate the tool, train colleagues or develop the UI.

---

## 15. REST API reference

The UI uses a JSON API that you can also script against. Interactive
documentation is served at **`/docs`** (Swagger UI) and **`/redoc`**.

All request and response bodies are JSON unless noted.

### 15.1 Metadata and health

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | `{"status":"ok"}`; used by the container health check. No sign-in required. |
| GET | `/api/meta` | Source tools, Datadog sites, category metadata, mapping rules |

### 15.2 Authentication

Every `/api/*` endpoint requires a session except `/api/health`,
`/api/auth/status`, `/api/auth/login` and `/api/auth/setup`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/auth/status` | `{"authenticated": bool, "setup_required": bool, "username": …}` |
| POST | `/api/auth/setup` | Create the first account; **409** once one exists |
| POST | `/api/auth/login` | Sign in; sets cookies and returns an API token |
| POST | `/api/auth/logout` | Revoke the current session |
| POST | `/api/auth/password` | `{current_password, new_password}`; revokes all sessions |
| GET | `/api/users` | List accounts |
| POST | `/api/users` | Create an account |
| DELETE | `/api/users/{id}` | Remove an account (not your own, not the last one) |

Two ways to authenticate:

- **Browser:** an HttpOnly session cookie. Non-GET requests must also send the
  `X-CSRF-Token` header matching the `dma_csrf` cookie (the UI does this
  automatically). Without it the request is refused with **403**.
- **Scripts:** the `token` returned by `/api/auth/login`, sent as
  `Authorization: Bearer <token>`. Bearer requests skip the CSRF check. The
  token is a session and expires with it.

```bash
TOKEN=$(curl -sf -X POST http://localhost:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"…"}' | jq -r .token)

curl -sf -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/projects
```

### 15.3 Migrations (projects)

| Method | Path | Description |
|---|---|---|
| GET | `/api/projects` | All migrations, each with its latest completed scan |
| POST | `/api/projects` | Create a migration |
| GET | `/api/projects/{id}` | One migration (secrets masked) |
| PUT | `/api/projects/{id}` | Update; blank secret fields keep stored values |
| DELETE | `/api/projects/{id}` | Delete the migration and all its scans |

Create or update body:

```json
{
  "name": "HQ NOC SolarWinds to Datadog",
  "source_tool": "solarwinds",
  "source": {
    "host": "orion.corp.example.com",
    "port": 17774,
    "username": "svc_datadog_migration",
    "password": "********",
    "verify_ssl": false,
    "demo": false
  },
  "datadog": {
    "site": "ddog-gov.com",
    "api_key": "********",
    "app_key": "********",
    "demo": false
  }
}
```

Masked response fragment:

```json
"source": { "host": "orion.corp.example.com", "password": "", "password_set": true }
```

### 15.4 Connection tests

| Method | Path | Description |
|---|---|---|
| POST | `/api/test/solarwinds` | Test a SolarWinds configuration |
| POST | `/api/test/datadog` | Test a Datadog configuration |

Body: `{"project_id": 1, "source": {...}, "datadog": {...}}`. `project_id` is
optional; when given, blank secret fields are filled from the stored
migration.

Both endpoints return HTTP 200. Check `ok`:

```json
{ "ok": true, "host": "orion.corp.example.com", "nodes": 1432, "modules": ["NPM 2024.2", "SAM 2024.2"] }
{ "ok": true, "site": "ddog-gov.com", "region": "US1-FED (GovCloud)", "hosts": 980 }
{ "ok": false, "error": "SolarWinds rejected the credentials (HTTP 401)" }
```

### 15.5 Scans

| Method | Path | Description |
|---|---|---|
| POST | `/api/projects/{id}/scans` | Start a scan. **409** if one is already running. |
| GET | `/api/projects/{id}/scans` | All scans for a migration, newest first (without item results) |
| GET | `/api/projects/{id}/trend` | Readiness per completed scan, overall and per category |
| GET | `/api/scans/{sid}` | Scan status, progress, message, summary, delta, warnings |
| DELETE | `/api/scans/{sid}` | Delete a scan |
| GET | `/api/scans/{sid}/items` | Mapped objects for one category |

`/api/scans/{sid}/items` query parameters:

| Parameter | Required | Values |
|---|---|---|
| `category` | yes | `nodes`, `interfaces`, `volumes`, `groups`, `custom_properties`, `applications`, `alerts`, `dependencies`, `snmp_devices` |
| `mapping` | no | `auto`, `review`, `unsupported` |
| `status` | no | `migrated`, `pending` |
| `q` | no | Free-text search across the whole object |
| `offset` | no | Default `0` |
| `limit` | no | Default `100`, maximum `1000` |

Response: `{"total": 252, "items": [ ... ]}`.

Scan status values: `running`, `complete`, `failed`.

### 15.6 Reports and exports

| Method | Path | Returns |
|---|---|---|
| GET | `/api/scans/{sid}/report` | Overall assessment (HTML). Add `?download=true` to download. |
| GET | `/api/scans/{sid}/report/{category}` | Category report (HTML). Supports `?download=true`. |
| GET | `/api/scans/{sid}/export/{category}.csv` | Category CSV (download) |
| GET | `/api/scans/{sid}/export.json` | Full scan JSON (download) |

These return **409** if the scan is not complete.

### 15.7 Scripting example

Run a scan every night and save the assessment report (requires `jq`):

```bash
#!/usr/bin/env bash
set -euo pipefail
BASE=http://localhost:8080
PID=1

TOKEN=$(curl -sf -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$DMA_USER\",\"password\":\"$DMA_PASS\"}" | jq -r .token)
auth=(-H "Authorization: Bearer $TOKEN")

SID=$(curl -sf "${auth[@]}" -X POST "$BASE/api/projects/$PID/scans" | jq -r .id)

while [ "$(curl -sf "${auth[@]}" "$BASE/api/scans/$SID" | jq -r .status)" = "running" ]; do
  sleep 15
done

STATUS=$(curl -sf "${auth[@]}" "$BASE/api/scans/$SID" | jq -r .status)
[ "$STATUS" = "complete" ] || { echo "scan $SID $STATUS"; exit 1; }

curl -sf "${auth[@]}" "$BASE/api/scans/$SID/report" -o "assessment-$(date +%F).html"
curl -sf "${auth[@]}" "$BASE/api/scans/$SID" | jq '.summary.overall | {readiness, migrated, pending}'
```

Cron entry:

```
0 2 * * * /opt/dma/nightly-scan.sh >> /var/log/dma-nightly.log 2>&1
```

---

## 16. Data model and storage

### 16.1 Database

SQLite at `DMA_DB_PATH` (default `/data/dma.sqlite3`).

**`projects`**

| Column | Type | Content |
|---|---|---|
| `id` | INTEGER | Primary key |
| `name` | TEXT | Migration name |
| `source_tool` | TEXT | `solarwinds` |
| `source_config` | TEXT | Encrypted JSON (SolarWinds connection) |
| `datadog_config` | TEXT | Encrypted JSON (Datadog connection) |
| `created_at`, `updated_at` | TEXT | ISO 8601 UTC |

**`users`**

| Column | Type | Content |
|---|---|---|
| `id` | INTEGER | Primary key |
| `username` | TEXT | Unique, case-insensitive on lookup |
| `password_hash` | TEXT | `pbkdf2_sha256$<iterations>$<salt>$<hash>` |
| `created_at`, `last_login` | TEXT | ISO 8601 UTC |

**`sessions`**

| Column | Type | Content |
|---|---|---|
| `id` | INTEGER | Primary key |
| `user_id` | INTEGER | Owning account |
| `token_hash` | TEXT | SHA-256 of the session token; the token itself is never stored |
| `created_at`, `expires_at` | TEXT | ISO 8601 UTC |

**`scans`**

| Column | Type | Content |
|---|---|---|
| `id` | INTEGER | Primary key |
| `project_id` | INTEGER | Owning migration |
| `status` | TEXT | `running`, `complete`, `failed` |
| `progress` | INTEGER | 0–100 |
| `message` | TEXT | Current stage or error |
| `started_at`, `finished_at` | TEXT | ISO 8601 UTC |
| `summary` | TEXT | JSON: `scores`, `delta`, `previous_scan` |
| `results` | TEXT | JSON: mapped items by category |
| `warnings` | TEXT | JSON list of strings |

Raw inventories are not stored; only mapped items (which include the relevant
source details).

### 16.2 Mapped item

```json
{
  "category": "applications",
  "source_id": 31,
  "source_name": "bos-sql-02 / SQL Server 2019",
  "mapping": "auto",
  "target_type": "Integration / Check",
  "target": "sqlserver",
  "migrated": true,
  "dd_match": "host:bos-sql-02.corp.example.com / sqlserver",
  "notes": "Enable the sqlserver integration.",
  "details": {
    "node": "bos-sql-02",
    "template": "SQL Server 2019",
    "components": 3,
    "component_names": "Service status, Response time, Process CPU"
  }
}
```

### 16.3 Direct queries

```bash
docker compose exec migration-assistant python - <<'EOF'
import sqlite3, json
c = sqlite3.connect("/data/dma.sqlite3")
for sid, status, summary in c.execute("SELECT id, status, summary FROM scans ORDER BY id"):
    o = (json.loads(summary or "{}").get("scores") or {}).get("overall", {})
    print(sid, status, o.get("readiness"))
EOF
```

---

## 17. Security

### 17.1 Credentials

- SolarWinds passwords and Datadog keys are encrypted with Fernet
  (AES-128-CBC with HMAC-SHA256). The encryption key is derived from the
  SHA-256 digest of `DMA_SECRET_KEY`, or of the generated secret in
  `/data/.dma_secret` (file mode `600`).
- Secrets are never returned by the API; responses contain empty strings plus
  `*_set` flags.
- If the secret changes or is lost, stored connections cannot be decrypted:
  the connections page will show empty fields and scans will fail with a
  credential error. Re-enter the credentials to recover. Scan history is not
  affected.
- Treat the data volume and `.env` as sensitive. Do not commit `.env`
  (it is excluded in `.dockerignore`).

### 17.2 Application access

- **Sign-in is required** for every API endpoint except the health check.
  Passwords are stored as PBKDF2-HMAC-SHA256 with a random salt and 600,000
  iterations, and compared in constant time.
- Sessions live in the database; only a SHA-256 hash of the token is stored.
  The session cookie is `HttpOnly` and `SameSite=Lax`, and is marked `Secure`
  when the request arrives over HTTPS (including through a proxy that sets
  `X-Forwarded-Proto`).
- Writes from the browser require a double-submit CSRF token.
- Sign-in attempts are throttled: 10 failures per username and IP address in
  15 minutes.
- All accounts are equal; there are no roles. Everyone who signs in can see
  and change every migration and every credential-free connection setting.
- Serve the app over HTTPS (see [Deployment guide](#18-deployment-guide)), so
  session cookies and passwords are never sent in clear text.

#### Resetting an account

There is no password reset link. To recover access:

```bash
docker compose exec migration-assistant python - <<'PY'
from app import auth, db
db.init()
user = auth.get_user("admin")            # existing account
auth.set_password(user["id"], "a-new-long-password")
# or, if every account is lost:
# auth.create_user("admin2", "a-new-long-password")
PY
```

### 17.3 Least privilege

- Use a read-only Orion account and a scoped Datadog Application key owned by
  a service account.
- The app only issues SWQL `SELECT` queries and HTTP `GET` requests to
  Datadog.

### 17.4 Transport

- Datadog calls always use HTTPS with certificate verification.
- SolarWinds calls use HTTPS; certificate verification is optional per
  migration. Enable it whenever Orion has a trusted certificate.
- The UI is served over plain HTTP; terminate TLS at the reverse proxy.

### 17.5 Container

- Runs as a non-root user (UID 10001).
- Only `/data` holds application state.
- Health check on `/api/health` every 30 seconds.

---

## 18. Deployment guide

### 18.1 Behind Nginx Proxy Manager

1. Bind the app to the Docker network only, or to `127.0.0.1:8080`.
2. Add a proxy host, for example `migration.example.com` → `http://<host>:8080`.
3. Request a certificate and force SSL.
4. Optional: add an access list or SSO forward-auth (Authentik, Authelia, or
   Keycloak via oauth2-proxy) in front of the built-in sign-in.

### 18.2 Behind plain Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name migration.example.com;

    ssl_certificate     /etc/ssl/certs/migration.crt;
    ssl_certificate_key /etc/ssl/private/migration.key;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host $host;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
```

### 18.3 Behind Traefik (labels)

```yaml
services:
  migration-assistant:
    # ...existing settings, with the ports: section removed...
    networks: [proxy]
    labels:
      - traefik.enable=true
      - traefik.http.routers.dma.rule=Host(`migration.example.com`)
      - traefik.http.routers.dma.entrypoints=websecure
      - traefik.http.routers.dma.tls.certresolver=letsencrypt
      - traefik.http.routers.dma.middlewares=authentik@docker
      - traefik.http.services.dma.loadbalancer.server.port=8080
networks:
  proxy:
    external: true
```

### 18.4 Bind mount instead of a named volume

```yaml
volumes:
  - ./data:/data
```

The container runs as UID 10001, so give it ownership first:

```bash
mkdir -p data && sudo chown 10001:10001 data
```

### 18.5 Air-gapped or restricted networks

1. Build and save the image on a connected machine:

   ```bash
   docker compose build
   docker save datadog-migration-assistant:1.0.0 | gzip > dma-1.0.0.tar.gz
   ```

2. Copy the archive, `docker-compose.yml` and `.env` to the target host.
3. Load and run:

   ```bash
   docker load < dma-1.0.0.tar.gz
   docker compose up -d        # no --build
   ```

The container needs outbound access only to Orion (17774) and the Datadog API
host for your site. For GovCloud this is `api.ddog-gov.com`.

### 18.6 Outbound HTTP proxy

httpx honours standard proxy variables. Add them to `.env`:

```bash
HTTPS_PROXY=http://proxy.corp.example.com:3128
NO_PROXY=orion.corp.example.com,localhost,127.0.0.1
```

Keep the Orion server in `NO_PROXY` if it is reached directly.

---

## 19. Backup, restore and upgrades

### Backup

```bash
docker compose stop
docker run --rm -v dma-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/dma-data-$(date +%F).tgz -C /data .
docker compose start
```

Compose may prefix the volume name with the project directory (for example
`datadog-migration-assistant_dma-data`). Check with `docker volume ls`.

Keep `.env` (with `DMA_SECRET_KEY`) with the backup; without it, stored
credentials cannot be decrypted on restore.

### Restore

```bash
docker compose down
docker run --rm -v dma-data:/data -v "$PWD":/backup alpine \
  sh -c "rm -rf /data/* /data/.dma_secret; tar xzf /backup/dma-data-YYYY-MM-DD.tgz -C /data && chown -R 10001:10001 /data"
docker compose up -d
```

### Upgrade

```bash
docker compose down
# replace the project files with the new version, keep .env
docker compose up -d --build
```

The database schema is created with `CREATE TABLE IF NOT EXISTS`, so existing
data is kept. Back up before upgrading.

### Housekeeping

Scan results accumulate. Delete old scans from **Scan history**, or via the
API (`DELETE /api/scans/{sid}`). SQLite does not shrink the file
automatically; reclaim space with:

```bash
docker compose exec migration-assistant python -c \
  "import sqlite3; sqlite3.connect('/data/dma.sqlite3').execute('VACUUM')"
```

---

## 20. Troubleshooting

### Connection tests

| Message | Likely cause | Fix |
|---|---|---|
| Login page says *Too many failed attempts* | Throttling after 10 failures | Wait for the 15-minute window to pass, or restart the container to clear it |
| *Invalid CSRF token* | Stale page after a restart or expired session | Reload the page and sign in again |
| Locked out with no working account | Password lost | See [Resetting an account](#resetting-an-account) |
| `Cannot reach <host> on the SWIS port` | Firewall, wrong host, or SWIS service stopped | Test `curl -k https://<host>:17774` from the Docker host; check the *SolarWinds Information Service V3* service on Orion |
| `SolarWinds rejected the credentials (HTTP 401)` | Wrong password, or account cannot log in to the web console | Log in to the Orion web console with the same account |
| `SWIS query timed out` | Very large estate or overloaded Orion database | Retry off-peak; see [Performance](#21-performance-and-limits) |
| TLS or certificate error | Verification enabled with a self-signed certificate | Turn off **Verify the server's TLS certificate**, or install a trusted certificate on Orion |
| `Unknown Datadog site` | Site value not in the supported list | Choose from the dropdown |
| `Datadog rejected the API or Application key` | Wrong key, or key belongs to a different site | Confirm the org's region matches the selected site |
| `API key is not valid for this site` | API key from another org or region | Use a key from the same org and site |
| `Access denied on /api/v1/validate…` | Outbound proxy or firewall blocking the call, or key restrictions | Check proxy settings and outbound access to `api.<site>` |

### Scans

| Symptom | Likely cause | Fix |
|---|---|---|
| Warning: `SolarWinds applications skipped: … Orion.APM.Application not found` | SAM not installed | Expected; applications will be empty |
| Warning: `SolarWinds interfaces skipped` | NPM not installed | Expected without NPM |
| Warning: `Datadog ndm devices skipped: Access denied…` | Application key lacks NDM permission | Add the permission, or ignore if NDM is not used |
| Warning: `Datadog services skipped` | No Service Catalog permission | Add `apm_service_catalog_read` |
| Scan status `failed`: `Interrupted by an application restart` | Container restarted during a scan | Run the scan again |
| **Run new scan** returns *Scan N is still running* | A scan is in progress | Wait for it to finish; progress is on the Overview page |
| A Windows server shows as *automatic* under SNMP devices | Orion does not report a Windows sysObjectID or "Windows" in vendor/machine type | Add the naming Orion uses to `WINDOWS_KEYWORDS` |
| A Linux server polled by SNMP is mapped to an NDM device | Orion does not report a Linux sysObjectID or recognizable vendor/machine type | Add the naming Orion uses to `LINUX_KEYWORDS`; see [Windows and Linux server detection](#116-windows-and-linux-server-detection) |
| A process monitor shows *pending* although a Datadog process monitor exists | The monitor's name and query do not mention the process name | Include the process name in the monitor query (`processes("java")…`) or its name |
| HTTP(S) component monitors show as *unsupported* | Application name matches no technology and no URL was read, so components were not recognized | Check scan warnings for `SAM component URLs not read`; run the [verification query](#verifying-https-components-in-swql); optionally set `DMA_SAM_HTTP_COMPONENT_TYPES` |
| Warning: `SAM component URLs not read` | The URL settings query failed | Run the [verification query](#verifying-https-components-in-swql) in SWQL Studio and compare the error |
| An HTTP(S) monitor shows *pending* although a test exists | URL differs (host or path), or the monitor query filters only by `instance:` | Align the URL, add `url:` to the monitor query, or name the test after the SAM application |
| An HTTP(S) monitor is *migrated* via "(same hostname)" but the path is not covered | Another test on that host matched | Add a test for the exact URL; the hostname fallback is a hint, not proof |
| Warning: `Datadog synthetics skipped` | Application key lacks `synthetics_read` | Add the scope |
| Everything shows *pending* although hosts exist in Datadog | Host names differ from Orion captions/DNS/SysName | Add Datadog host aliases, or adjust `norm_host` |
| Alerts never match | Monitor names differ from alert names | Tag monitors with `sw_alert_id:<AlertID>` |
| Custom property shows *unsupported* but has values | Property name contains spaces or symbols | See [Known limitations](#25-known-limitations-and-roadmap) |
| Inventory smaller than expected | Orion account limitations | Use an account without limitations |
| Readiness dropped since the last scan | Regressions | Check *regressed* counts in the category report and the scan warnings |

### Logs

```bash
docker compose logs -f migration-assistant
```

Set `DMA_LOG_LEVEL=DEBUG` in `.env` and restart for more detail. Failed SWIS
categories and scan crashes (with stack traces) are logged.

### UI shows an old version after an upgrade

Hard-refresh the browser (Ctrl+Shift+R / Cmd+Shift+R) to reload `app.js` and
`app.css`.

---

## 21. Performance and limits

| Item | Value |
|---|---|
| SWIS request timeout | 120 seconds per query |
| Datadog request timeout | 60 seconds per request |
| Datadog 429 retries | 5, waiting 1–60 seconds each |
| NDM interface lookups | First 1,000 NDM devices (one request per device) |
| Group members stored | 500 per group |
| Custom property samples | 8 most common values stored (5 shown in reports) |
| Newly migrated / regressed names stored | 50 per category |
| Items per API page | 100 by default, 1,000 maximum |
| Concurrent scans | One per migration; different migrations can scan in parallel |

Rough scan durations (depend heavily on Orion database load and network
latency):

| Estate | Duration |
|---|---|
| Demo data | 1–2 seconds |
| ~1,000 nodes, 20,000 interfaces | 1–3 minutes |
| ~5,000 nodes, 100,000 interfaces | 5–15 minutes, dominated by the SWIS interface query and NDM interface lookups |

Tips for large estates:

- Scan outside business hours; the SWIS interface and custom property queries
  are the heaviest on the Orion database.
- Scope the Orion account with limitations to migrate site by site.
- Delete scans you no longer need to keep the database small.

---

## 22. Development

### Run locally without Docker

Requires Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

mkdir -p data
export DMA_DB_PATH=./data/dma.sqlite3
export DMA_SECRET_KEY=dev-only-secret
export DMA_ADMIN_USER=dev DMA_ADMIN_PASSWORD=dev-password-123
uvicorn app.main:app --reload --port 8080
```

Open `http://localhost:8080` and use demo data.

### Front end

- `app/static/index.html` — page shell.
- `app/static/app.css` — design tokens at the top (`:root`) and all styles.
  Colors: background `#15121d`, panels `#1d1a27`, accents `#9467ec` and
  `#632ca6`, automatic `#3fc6a2`, review `#f0b545`, unsupported `#ec6f6f`,
  migrated `#7fd46b`.
- `app/static/app.js` — hash router and views:

| Route | View |
|---|---|
| `#/` | Login, or the start page when signed in |
| `#/account` | Account: password and other accounts |
| `#/new/solarwinds` | New migration |
| `#/p/{id}` | Overview |
| `#/p/{id}/inventory/{category}` | Inventory |
| `#/p/{id}/rules` | Mapping rules |
| `#/p/{id}/reports[/{scanId}]` | Reports |
| `#/p/{id}/history` | Scan history |
| `#/p/{id}/settings` | Connections |

No build step or package manager is involved; edit and reload.

### Testing connectors without real systems

Both connectors expose their `httpx.Client` as `.http`, so you can replace it
with a mock transport:

```python
import httpx
from app.connectors.solarwinds import SolarWindsClient

def handler(request):
    return httpx.Response(200, json={"results": [{"C": 3}]})

sw = SolarWindsClient("orion.test", "u", "p")
sw.http = httpx.Client(transport=httpx.MockTransport(handler))
print(sw.test())    # {'ok': True, 'host': 'orion.test', 'nodes': 3, 'modules': [...]}
```

---

## 23. Extending the application

### 23.1 Adjust a mapping rule

1. Change the classification logic in the relevant `map_*` function in
   `app/mapping/engine.py`.
2. Update the matching entry in `RULES` in `app/mapping/rules.py` so the
   Mapping rules page and reports describe the new behaviour.

### 23.2 Add a SAM technology

Add a tuple to `SAM_INTEGRATIONS` in `app/mapping/rules.py`. Place more
specific keywords **above** generic ones (for example `tomcat` above
`apache`, and any product whose name contains "web" above the `http_check`
entry):

```python
(("sharepoint",), "sharepoint"),
```

### 23.3 Change host name matching

`norm_host` in `app/mapping/engine.py` controls how names are compared. To
match on fully qualified names instead of short names:

```python
def norm_host(value: str) -> str:
    return (value or "").strip().lower()
```

### 23.4 Add a Datadog category

1. Add a reader method to `DatadogClient` and register it in the `steps` list
   inside `collect`.
2. Index it in `Index.__init__` in `engine.py`.
3. Use it in the relevant `map_*` function.

### 23.5 Add a source platform (Zabbix, Checkmk, …)

The current mapping engine is SolarWinds-specific. A new source needs:

1. A connector in `app/connectors/<tool>.py` with `test()` and
   `collect(progress)` methods.
2. Mapping functions and rules for that tool's object model (for example a
   new `app/mapping/<tool>.py`, with rules in the same shape as `RULES`).
3. A branch in `_run_scan` and `test_connection` in `app/main.py`, selected by
   the migration's `source_tool`, and removal of the SolarWinds-only check in
   `create_project`.
4. `available: True` for the tool in `TOOLS` in `app/main.py`.
5. A connection form for the tool in `viewConnect` in `app/static/app.js`.

Scoring, comparison, reports, storage and UI views work with any source as
long as the mapped items follow the [item shape](#162-mapped-item) and the
category metadata follows `CATEGORY_META`.

---

## 24. Project layout

```
datadog-migration-assistant/
├── Dockerfile                 Python 3.12 slim image, non-root, health check
├── docker-compose.yml         Service, port 8080, dma-data volume
├── .env.example               Environment variable template
├── .dockerignore
├── requirements.txt           fastapi, uvicorn, httpx, pydantic, cryptography
├── README.md
└── app/
    ├── main.py                API routes, auth middleware, scan worker, static hosting
    ├── auth.py                Accounts, password hashing, sessions, throttling
    ├── db.py                  SQLite helpers and schema
    ├── crypto.py              Credential encryption and masking
    ├── demo.py                Demo inventories
    ├── reports.py             HTML reports and CSV
    ├── connectors/
    │   ├── solarwinds.py      SWIS client and SWQL inventory
    │   └── datadog.py         Datadog client and inventory
    ├── mapping/
    │   ├── rules.py           Category metadata, rules, SAM keyword map
    │   └── engine.py          Classification, matching, scoring, comparison
    └── static/
        ├── index.html
        ├── app.css
        └── app.js
```

---

## 25. Known limitations and roadmap

### Limitations in 1.0

- **Single source platform.** Only SolarWinds Orion is implemented.
- **Authentication is basic by design:** local accounts only, no roles, no
  SSO or MFA. Put SSO in front of it with a reverse proxy if you need that.
- **Custom property names with spaces or symbols** are listed, but their
  values are not read, so they are classified as unsupported.
- **Alert translation is not assessed.** Matching uses IDs and names;
  thresholds, trigger conditions and actions are not compared.
- **SAM mapping is keyword-based** on template and application names, not on
  component types.
- **Windows servers polled by SNMP** are excluded from NDM in the SNMP
  devices category, but the Nodes rules still map them to an NDM device (only
  SNMP-polled *Linux* servers are mapped to an Agent host there).
- **HTTP(S) monitor matching** checks that a test or monitor exists for the
  URL, not that its assertions, frequency or locations match SolarWinds. The
  hostname fallback can mark a monitor migrated when only another path on the
  same host is tested. Multistep API tests are not read. Use the **Match
  quality** column to separate exact URL hits from weaker matches.
- **Process monitor matching** looks for the process name in a Datadog process
  monitor's name or query, or for the `process` integration on the host. It
  does not compare thresholds, command-line arguments or user context.
- **Short-name host matching** can produce false matches when the same short
  name exists in several domains.
- **NDM interface lookups** stop after 1,000 devices per scan.
- **Dependencies** are always *needs review* when resolvable.
- **Overall readiness** weights categories by object count.
- **No scheduling.** Use the API with cron (see
  [Scripting example](#157-scripting-example)).
- A container restart during a scan marks that scan as failed.

### Roadmap

- Zabbix, Checkmk, New Relic, Nagios XI and PRTG connectors.
- OIDC or SAML single sign-on, and roles (read-only versus editor).
- Scheduled scans and email or Slack summaries.
- Alert threshold extraction and suggested monitor definitions (Terraform or
  JSON).
- Generated Agent configuration (`conf.d`) and NDM autodiscovery snippets.
- Weighted readiness with per-category weights.
- Assignment and sign-off of *needs review* items.
- Manual mapping overrides stored per object.
