"""Bounded A510 :8000 read-only maintenance collection and streaming log analysis.

The export endpoint compresses logs on the appliance. Do not run frequently.
Protocol follows A510 DeviceWeb login, querySystemInfo and operationsLogDownLoad.
No raw log lines, credentials, license payloads or sessions leave this module.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo


class MaintenanceError(Exception):
    """Bounded, credential-free failure."""


class MaintenanceClient:
    def __init__(self, host, username, password, *, port=8000, scheme="http", timeout=15):
        if scheme not in ("http", "https") or not host or any(c in host for c in "/@?#"):
            raise ValueError("Invalid appliance endpoint")
        self.host, self.port, self.scheme = host, int(port), scheme
        self.username, self.password = username, password
        self.timeout = min(max(float(timeout), 1), 30)
        self.token = self.session = ""
        self.base = f"{scheme}://{host}:{self.port}/"

    def _request(self, method, path, *, body=None, timeout=None):
        connection_class = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        conn = connection_class(self.host, self.port, timeout=timeout or self.timeout)
        headers = {"Content-Type": "application/xml", "Cookie": f"sessionId={self.session}"}
        if self.token:
            headers["token"] = self.token
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            if response.status != 200:
                raise MaintenanceError(f"Appliance HTTP status {response.status}")
            return conn, response
        except Exception:
            conn.close()
            raise MaintenanceError("Appliance request failed") from None

    def _post(self, endpoint, content="", timeout=None):
        token = f"<token>{escape(self.token)}</token>" if self.token else ""
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<request version="1.0" systemType="NVMS-5000" clientType="WEB">'
            f"{token}{content}</request>"
        ).encode()
        conn, response = self._request("POST", "/" + endpoint, body=body, timeout=timeout)
        try:
            payload = response.read(2_000_001)
            if len(payload) > 2_000_000 or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
                raise MaintenanceError("Invalid appliance XML")
            root = ET.fromstring(payload)
            if root.findtext("status") != "success":
                raise MaintenanceError("Appliance rejected request; no retry attempted")
            return root
        except (ET.ParseError, OSError):
            raise MaintenanceError("Invalid appliance response") from None
        finally:
            conn.close()

    def login(self):
        challenge = self._post("reqLogin")
        self.token = challenge.findtext("content/token") or ""
        self.session = (challenge.findtext("content/sessionId") or "").strip("{}")
        nonce = challenge.findtext("content/nonce") or ""
        if not self.token or not self.session or not nonce or any(c in self.session + self.token for c in "\r\n"):
            raise MaintenanceError("Incomplete appliance challenge")
        password_hash = hashlib.md5(self.password.encode(), usedforsecurity=False).hexdigest()
        digest = hashlib.sha256((password_hash + nonce).encode()).hexdigest()
        # DeviceWeb sends the token twice on doLogin.
        self._post(
            "doLogin",
            f"<token>{escape(self.token)}</token><content><userName>{escape(self.username)}</userName><password>{digest}</password><language>en</language></content>",
        )

    def logout(self):
        try:
            if self.token:
                self._post("doLogout")
        except MaintenanceError:
            pass  # A failed cleanup must not replace the collection result.
        finally:
            self.token = self.session = ""

    def system_info(self):
        root = self._post("querySystemInfo", "<condition><langType>en</langType></condition>")
        fields = ("productModel", "softwareVersion", "updateTime", "serverTime", "serverRunTime", "cpu", "memory")
        return {field: (root.findtext("content/" + field) or "")[:160] for field in fields}

    def download(self, target, *, max_bytes=1_000_000_000, deadline_seconds=900):
        deadline = time.monotonic() + deadline_seconds
        root = self._post("operationsLogDownLoad", timeout=min(300, deadline_seconds))
        url = urlsplit(urljoin(self.base, root.findtext("content/logPackageUrl") or ""))
        expected = urlsplit(self.base)
        if (
            not url.path
            or url.path == "/"
            or (url.scheme, url.hostname, url.port) != (expected.scheme, expected.hostname, expected.port)
            or url.username
            or url.password
        ):
            raise MaintenanceError("Export URL is missing or not on the appliance origin")
        conn, response = self._request("GET", url.path + ("?" + url.query if url.query else ""))
        digest = hashlib.sha256()
        size = 0
        try:
            while True:
                if time.monotonic() > deadline:
                    raise MaintenanceError("Export download deadline exceeded")
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise MaintenanceError("Export exceeds compressed size limit")
                digest.update(block)
                target.write(block)
        finally:
            conn.close()
        target.seek(0)
        return digest.hexdigest(), size


def analyze_archive(archive, *, server, tz_name, start, end, source_sha256, deadline_seconds=600):
    """Stream ZIP members without extraction; count messages, never unique alarms."""
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError("Supply an aware, ordered time window")
    zone = ZoneInfo(tz_name)
    counts = Counter()
    coverage = {}
    license_latest = None
    file_count = 0
    oversized_lines = 0
    deadline = time.monotonic() + deadline_seconds
    stamp = re.compile(rb"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > 512 or sum(x.file_size for x in members) > 8_000_000_000:
            raise MaintenanceError("Archive exceeds expanded size/file limits")
        for member in members:
            if ".log" not in member.filename or member.is_dir():
                continue
            service_match = re.search(r"(?:^|/)([A-Za-z]+Server|MonitorClient)_log/", member.filename)
            if not service_match:
                continue
            service = service_match[1]
            file_count += 1
            current = None
            last_stamp = None
            parsed_stamp = None
            with bundle.open(member) as stream:
                while True:
                    if time.monotonic() > deadline:
                        raise MaintenanceError("Log analysis deadline exceeded")
                    line = stream.readline(16385)
                    if not line:
                        break
                    if len(line) > 16384:
                        oversized_lines += 1
                        while line and not line.endswith(b"\n"):
                            if time.monotonic() > deadline:
                                raise MaintenanceError("Log analysis deadline exceeded")
                            line = stream.readline(16385)
                        current = None
                        continue
                    match = stamp.match(line)
                    if match:
                        if match[1] != last_stamp:
                            last_stamp = match[1]
                            try:
                                parsed_stamp = datetime.fromisoformat(last_stamp.decode()).replace(tzinfo=zone)
                            except ValueError:
                                parsed_stamp = None
                        current = parsed_stamp
                    if current is None or not start <= current <= end:
                        continue
                    bounds = coverage.setdefault(service, [current, current])
                    bounds[0], bounds[1] = min(bounds[0], current), max(bounds[1], current)
                    recognized = False
                    for needle, code in [
                        (b"not enough thread", "worker_capacity"),
                        (b"Decode device count out", "decoder_overrun"),
                        (b"0x2000001c", "registration_failure"),
                        (b"Fun_ReStart", "service_restart"),
                        (b"does not have device authority", "device_authority"),
                        ("匹配到联动信息加入联动队列".encode(), "linkage_volume"),
                        (b"disconnet miss heartbeat", "heartbeat_disconnect"),
                        (b"false == InitSocks", "multicast_failure"),
                    ]:
                        if needle in line:
                            subject = service
                            if code == "device_authority":
                                user = re.search(rb"- ([A-Za-z0-9_.@-]{1,128}) does not have device authority", line)
                                if user:
                                    subject = user[1].decode()
                            counts[(service, code, subject)] += 1
                            recognized = True
                    if any(level in line for level in (b"] ERROR", b"] WARN", b"] FATAL")) and not recognized:
                        counts[(service, "unclassified_errors", service)] += 1
                    license_match = re.search(rb"<maxDecodeOutputCount>(\d+)</maxDecodeOutputCount>", line)
                    if license_match and (license_latest is None or current >= license_latest[0]):
                        license_latest = (current, int(license_match[1]))
    observations = []
    for (service, code, subject), value in sorted(counts.items()):
        lo, hi = coverage[service]
        observations.append(
            dict(
                server=server,
                subject=subject,
                code=code,
                value=value,
                window_start=lo.isoformat(),
                window_end=hi.isoformat(),
                source_sha256=source_sha256,
            )
        )
    if license_latest:
        at, value = license_latest
        observations.append(
            dict(
                server=server,
                subject="",
                code="decoder_outputs",
                value=value,
                window_start=at.isoformat(),
                window_end=at.isoformat(),
                source_sha256=source_sha256,
            )
        )
    return {"schema": 1, "observations": observations}, {
        "files": file_count,
        "oversized_lines_skipped": oversized_lines,
        "coverage": {k: [v[0].isoformat(), v[1].isoformat()] for k, v in coverage.items()},
    }


def collect_audit(client, *, tz_name, start, end):
    """Login once, capture pre/post resources, download and analyze temporary ZIP."""
    client.login()
    try:
        before = client.system_info()
        with tempfile.TemporaryFile() as archive:
            digest, size = client.download(archive)
            after = client.system_info()
            evidence, meta = analyze_archive(
                archive,
                server=client.host,
                tz_name=tz_name,
                start=start,
                end=end,
                source_sha256=digest,
            )
    finally:
        client.logout()
    meta.update(archive_sha256=digest, compressed_bytes=size, before=before, after=after)
    for label, sample in (("before export", before), ("after export", after)):
        try:
            at = datetime.fromisoformat(sample["serverTime"]).replace(tzinfo=ZoneInfo(tz_name))
            value = float(sample["cpu"].rstrip("%"))
        except (ValueError, KeyError):
            continue
        evidence["observations"].append(
            dict(
                server=client.host,
                subject=label,
                code="cpu_used",
                value=value,
                window_start=at.isoformat(),
                window_end=at.isoformat(),
                source_sha256=hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest(),
            )
        )
    return evidence, meta
