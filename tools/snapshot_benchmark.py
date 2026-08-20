#!/usr/bin/env python3
"""Benchmark the TVT snapshot transports against one real device.

Runs each available capture leg N times against the same channel and reports
latency (min/median/max), payload size, parsed JPEG dimensions, and a coarse
resource signal (RSS delta, whether an ffmpeg subprocess was needed), so the
legs can be compared on real hardware before changing routing policy.

Legs:

- ``webapi``  — LAPI ``GetSnapshot`` (the NVR's "API Server" service):
                one authenticated HTTP GET, no subprocess, no SDK login.
- ``rtsp``    — RTSP frame grab via ffmpeg (URL resolved over the web CGI).
- ``netsdk``  — native ``NET_SDK_CaptureJPEGData_V2`` (requires the vendor
                SDK installed; skipped automatically when unavailable).

Usage::

    python tools/snapshot_benchmark.py --ip 192.168.1.100 -p 'Password' \
        --channel 1 --iterations 5

The device password can also come from ``TVT_PASSWORD``. Output is a table on
stdout plus optional ``--json`` for machine consumption.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import resource
import statistics
import struct
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def _jpeg_dimensions(payload: bytes) -> tuple[int | None, int | None]:
    """Parse width/height from JPEG SOF markers; (None, None) if unparseable."""
    if len(payload) < 4 or payload[0:2] != b"\xff\xd8":
        return None, None
    idx = 2
    while idx + 9 < len(payload):
        if payload[idx] != 0xFF:
            idx += 1
            continue
        marker = payload[idx + 1]
        # SOF0..SOF15 excluding DHT(C4)/JPG(C8)/DAC(CC)
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", payload[idx + 5 : idx + 9])
            return width, height
        length = struct.unpack(">H", payload[idx + 2 : idx + 4])[0]
        idx += 2 + length
    return None, None


@dataclass
class LegReport:
    leg: str
    ok: int = 0
    failed: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)
    width: int | None = None
    height: int | None = None
    rss_delta_kb: int = 0
    uses_subprocess: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        lat = self.latencies_ms
        return {
            "leg": self.leg,
            "ok": self.ok,
            "failed": self.failed,
            "latency_ms": {
                "min": round(min(lat), 1) if lat else None,
                "median": round(statistics.median(lat), 1) if lat else None,
                "max": round(max(lat), 1) if lat else None,
            },
            "payload_bytes": {
                "min": min(self.sizes) if self.sizes else None,
                "median": int(statistics.median(self.sizes)) if self.sizes else None,
                "max": max(self.sizes) if self.sizes else None,
            },
            "resolution": (f"{self.width}x{self.height}" if self.width else None),
            "rss_delta_kb": self.rss_delta_kb,
            "uses_subprocess": self.uses_subprocess,
            "errors": self.errors[:3],
        }


def _rss_kb() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux kilobytes.
    return usage // 1024 if sys.platform == "darwin" else usage


def _run_leg(report: LegReport, iterations: int, capture) -> LegReport:
    rss_before = _rss_kb()
    for _ in range(iterations):
        t0 = time.monotonic()
        try:
            payload = capture()
        except Exception as exc:
            report.failed += 1
            report.errors.append(f"{type(exc).__name__}: {exc}")
            continue
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        if not payload:
            report.failed += 1
            report.errors.append("empty payload")
            continue
        report.ok += 1
        report.latencies_ms.append(elapsed_ms)
        report.sizes.append(len(payload))
        if report.width is None:
            report.width, report.height = _jpeg_dimensions(payload)
    report.rss_delta_kb = max(0, _rss_kb() - rss_before)
    return report


def bench_webapi(args) -> LegReport:
    from pytvt.web_api.client import WebApiClient

    report = LegReport(leg="webapi")
    client = WebApiClient(
        args.ip,
        args.username,
        args.password,
        port=args.web_port,
        timeout=args.timeout,
    )

    def capture() -> bytes | None:
        result = client.get_snapshot_webapi(channel_id=args.channel)
        if not result.success:
            raise RuntimeError(result.error or "webapi capture failed")
        return result.image_data

    return _run_leg(report, args.iterations, capture)


def bench_rtsp(args) -> LegReport:
    from pytvt.xml_api import NvrClient, rtsp_snapshot_attempt_bytes

    report = LegReport(leg="rtsp", uses_subprocess=True)
    with NvrClient(args.ip, args.username, args.password, port=args.web_port, timeout=args.timeout) as nvr:
        nvr.login()
        rtsp_url = nvr.get_rtsp_url(args.channel, "main")
    if not rtsp_url:
        report.failed = args.iterations
        report.errors.append("could not resolve an RTSP URL over the web CGI")
        return report

    def capture() -> bytes | None:
        frame = rtsp_snapshot_attempt_bytes(rtsp_url, timeout=args.timeout)
        if not frame.image:
            raise RuntimeError(frame.error or "rtsp frame grab failed")
        return frame.image

    return _run_leg(report, args.iterations, capture)


def bench_netsdk(args) -> LegReport:
    report = LegReport(leg="netsdk")
    try:
        from pytvt.device_sdk.client import Client
    except Exception as exc:
        report.failed = args.iterations
        report.errors.append(f"NetSDK unavailable: {type(exc).__name__}: {exc}")
        return report

    try:
        client = Client()
        session = client.login(args.ip, args.sdk_port, args.username, args.password)
    except Exception as exc:
        report.failed = args.iterations
        report.errors.append(f"NetSDK login failed: {type(exc).__name__}: {exc}")
        return report

    def capture() -> bytes | None:
        return session.capture_jpeg(args.channel - 1)  # NetSDK is 0-based

    try:
        return _run_leg(report, args.iterations, capture)
    finally:
        with contextlib.suppress(Exception):
            session.logout()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ip", required=True, help="Device IP address")
    parser.add_argument("-u", "--username", default=os.getenv("TVT_USERNAME", "admin"))
    parser.add_argument("-p", "--password", default=os.getenv("TVT_PASSWORD", ""))
    parser.add_argument("-c", "--channel", type=int, default=1, help="Channel (1-based, web numbering)")
    parser.add_argument("-n", "--iterations", type=int, default=5)
    parser.add_argument("--web-port", type=int, default=80)
    parser.add_argument("--sdk-port", type=int, default=6036)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument(
        "--legs",
        default="webapi,rtsp,netsdk",
        help="Comma-separated legs to run (webapi,rtsp,netsdk)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    args = parser.parse_args()

    if not args.password:
        parser.error("password required (-p or TVT_PASSWORD)")

    bench_fns = {"webapi": bench_webapi, "rtsp": bench_rtsp, "netsdk": bench_netsdk}
    reports: list[LegReport] = []
    for leg in (leg.strip() for leg in args.legs.split(",")):
        fn = bench_fns.get(leg)
        if fn is None:
            print(f"unknown leg: {leg}", file=sys.stderr)
            return 2
        print(f"[{leg}] running {args.iterations} captures against {args.ip} ch{args.channel} ...", file=sys.stderr)
        reports.append(fn(args))

    if args.json:
        print(json.dumps([r.as_dict() for r in reports], indent=2))
        return 0

    header = f"{'leg':<8} {'ok':>3} {'fail':>4} {'lat min/med/max (ms)':>24} {'size (median B)':>16} {'resolution':>12} {'rss Δ(KB)':>10} {'subproc':>8}"
    print(header)
    print("-" * len(header))
    for r in reports:
        d = r.as_dict()
        lat = d["latency_ms"]
        lat_s = f"{lat['min']}/{lat['median']}/{lat['max']}" if lat["median"] is not None else "-"
        size_s = str(d["payload_bytes"]["median"] or "-")
        print(
            f"{r.leg:<8} {r.ok:>3} {r.failed:>4} {lat_s:>24} {size_s:>16} "
            f"{d['resolution'] or '-':>12} {r.rss_delta_kb:>10} {'yes' if r.uses_subprocess else 'no':>8}"
        )
        for err in d["errors"]:
            print(f"         ! {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
