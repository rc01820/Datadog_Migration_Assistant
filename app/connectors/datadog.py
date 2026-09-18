"""Datadog API connector (read-only). Needs an API key and an Application key
with read scopes for hosts, monitors, dashboards, NDM and the service catalog."""
from __future__ import annotations

import logging
import time
from typing import Callable

import httpx

log = logging.getLogger("dma.datadog")

SITES = {
    "datadoghq.com": "US1",
    "us3.datadoghq.com": "US3",
    "us5.datadoghq.com": "US5",
    "datadoghq.eu": "EU1",
    "ap1.datadoghq.com": "AP1",
    "ap2.datadoghq.com": "AP2",
    "ddog-gov.com": "US1-FED (GovCloud)",
}


class DatadogError(Exception):
    pass


class DatadogClient:
    def __init__(self, site: str, api_key: str, app_key: str, timeout: int = 60,
                 max_ndm_interface_devices: int = 1000):
        site = (site or "datadoghq.com").strip().replace("https://", "").replace("app.", "").rstrip("/")
        if site not in SITES:
            raise DatadogError(f"Unknown Datadog site '{site}'")
        if not api_key or not app_key:
            raise DatadogError("Datadog API key and Application key are both required")
        self.site = site
        self.base = f"https://api.{site}"
        self.max_ndm = max_ndm_interface_devices
        self.http = httpx.Client(timeout=timeout, headers={
            "DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key, "Accept": "application/json"})

    def close(self):
        self.http.close()

    def _get(self, path: str, params: dict | None = None, allow_404=False) -> dict:
        for attempt in range(5):
            try:
                r = self.http.get(self.base + path, params=params)
            except httpx.HTTPError as e:
                raise DatadogError(f"Cannot reach {self.base}: {e}") from e
            if r.status_code == 429:
                wait = int(r.headers.get("X-RateLimit-Reset", "5") or 5)
                log.info("rate limited on %s, waiting %ss", path, wait)
                time.sleep(min(max(wait, 1), 60))
                continue
            if allow_404 and r.status_code == 404:
                return {}
            if r.status_code == 403:
                raise DatadogError(f"Access denied on {path}. The Application key is missing a read scope.")
            if r.status_code == 401:
                raise DatadogError("Datadog rejected the API or Application key")
            if r.status_code >= 400:
                raise DatadogError(f"Datadog {path} returned HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        raise DatadogError(f"Datadog kept rate-limiting {path}")

    # ------------------------------------------------------------------ test
    def test(self) -> dict:
        if not self._get("/api/v1/validate").get("valid"):
            raise DatadogError("API key is not valid for this site")
        self._get("/api/v1/monitor", {"page": 0, "page_size": 1})
        hosts = self._get("/api/v1/hosts", {"count": 1})
        return {"ok": True, "site": self.site, "region": SITES[self.site],
                "hosts": hosts.get("total_matching", 0)}

    # ------------------------------------------------------------ inventory
    def collect(self, progress: Callable[[int, str], None]) -> tuple[dict, list[str]]:
        inv = {"hosts": [], "ndm_devices": [], "monitors": [], "dashboards": [], "tags": {}, "services": [],
               "synthetics": []}
        warnings = []
        steps = [("hosts", self._hosts), ("tags", self._tags), ("monitors", self._monitors),
                 ("dashboards", self._dashboards), ("ndm_devices", self._ndm), ("services", self._services),
                 ("synthetics", self._synthetics)]
        for i, (name, fn) in enumerate(steps):
            progress(int(58 + i * 30 / len(steps)), f"Reading Datadog {name.replace('_', ' ')}")
            try:
                inv[name] = fn()
            except DatadogError as e:
                warnings.append(f"Datadog {name.replace('_', ' ')} skipped: {e}")
        return inv, warnings

    def _hosts(self):
        out, start = [], 0
        while True:
            page = self._get("/api/v1/hosts", {"start": start, "count": 1000, "include_hosts_metadata": "true"})
            rows = page.get("host_list", [])
            for h in rows:
                tags = sorted({t for src in (h.get("tags_by_source") or {}).values() for t in src})
                out.append({"name": h.get("host_name") or h.get("name") or "",
                            "aliases": h.get("aliases") or [], "apps": h.get("apps") or [],
                            "sources": h.get("sources") or [], "tags": tags,
                            "platform": (h.get("meta") or {}).get("platform", "")})
            start += len(rows)
            if not rows or start >= page.get("total_matching", 0):
                return out

    def _tags(self):
        return self._get("/api/v1/tags/hosts").get("tags", {})

    def _monitors(self):
        out, page = [], 0
        while True:
            rows = self._get("/api/v1/monitor", {"page": page, "page_size": 1000})
            for m in rows:
                out.append({"id": m.get("id"), "name": m.get("name") or "", "type": m.get("type"),
                            "query": m.get("query") or "", "tags": m.get("tags") or []})
            if len(rows) < 1000:
                return out
            page += 1

    def _dashboards(self):
        rows = self._get("/api/v1/dashboard").get("dashboards", [])
        return [{"id": d.get("id"), "title": d.get("title") or ""} for d in rows]

    def _ndm(self):
        out, page = [], 0
        while True:
            body = self._get("/api/v2/ndm/devices", {"page[number]": page, "page[size]": 500}, allow_404=True)
            rows = body.get("data", [])
            for d in rows:
                a = d.get("attributes", {})
                out.append({"id": d.get("id"), "name": a.get("name") or "", "ip": a.get("ip_address") or "",
                            "vendor": a.get("vendor") or "", "model": a.get("model") or "",
                            "status": a.get("status") or "", "tags": a.get("tags") or [], "interfaces": []})
            if len(rows) < 500:
                break
            page += 1
        for d in out[: self.max_ndm]:
            try:
                body = self._get("/api/v2/ndm/interfaces", {"device_id": d["id"]}, allow_404=True)
                d["interfaces"] = [(i.get("attributes") or {}).get("name", "") for i in body.get("data", [])]
            except DatadogError:
                continue
        return out

    def _synthetics(self):
        """Synthetic API HTTP tests and browser tests, with their target URL."""
        out, page = [], 0
        while True:
            body = self._get("/api/v1/synthetics/tests", {"page_size": 100, "page_number": page}, allow_404=True)
            rows = body.get("tests", [])
            for t in rows:
                ttype, sub = t.get("type"), t.get("subtype")
                if not (ttype == "browser" or (ttype == "api" and sub in (None, "http"))):
                    continue
                req = (t.get("config") or {}).get("request") or {}
                out.append({"id": t.get("public_id"), "name": t.get("name") or "",
                            "type": "browser" if ttype == "browser" else "http",
                            "url": req.get("url") or "", "tags": t.get("tags") or []})
            if len(rows) < 100:
                return out
            page += 1

    def _services(self):
        out, page = [], 0
        while True:
            body = self._get("/api/v2/services/definitions", {"page[number]": page, "page[size]": 100},
                             allow_404=True)
            rows = body.get("data", [])
            for s in rows:
                schema = (s.get("attributes") or {}).get("schema") or {}
                name = schema.get("dd-service") or (schema.get("info") or {}).get("dd-service") or ""
                deps = schema.get("dependsOn") or (schema.get("extensions") or {}).get("dependsOn") or []
                out.append({"name": name, "depends_on": [str(x).split(":")[-1] for x in deps]})
            if len(rows) < 100:
                return out
            page += 1
