#!/usr/bin/env python3
"""TVT Camera Snapshot & RTSP URL Capture.

Two operating modes
-------------------
``--direct``  Pure-Python via NVR web API (port 80) + ffmpeg RTSP capture.
              No Docker container needed.  Logs into the NVR, queries
              channels, then captures each IPC camera directly.

*(default)*   Via an SDK HTTP bridge service that wraps the TVT SDK.
              Required for IPC cameras that aren't behind an NVR, or when
              you need SDK-specific operations (e.g. changing IPC passwords).

Scope & limitations
-------------------
- ``--direct`` mode only works with **NVR devices** running NVMS-9000.
  It does NOT work with standalone IPC cameras (different web interface).
- For standalone IPC cameras, use the default SDK bridge mode
    (connects on port 9008).
- The NVR's own RTSP relay returns 401; direct mode bypasses it by
  connecting to each IPC camera's RTSP stream at ``rtsp://…@IPC_IP:554/profile1``.

Usage
-----
::

    # --- Direct mode (NVR web API — no Docker) ---
    python tvt_snapshot.py --direct --ip 192.168.1.100 --all-channels -o ./snapshots/ -p 'YourPassword'
    python tvt_snapshot.py --direct --ip 192.168.1.100 --channel 3 -o snap.jpg -p 'YourPassword'
    python tvt_snapshot.py --direct --from-json nvrs.json --all-channels -o ./snapshots/ -p 'YourPassword'

    # --- SDK mode (external SDK bridge — works with NVRs and IPCs) ---
    python tvt_snapshot.py --ip 192.168.1.100 --channel 0 -o snapshot.jpg
    python tvt_snapshot.py --ip 192.168.1.100 --all-channels --max-channels 16 -o ./snapshots/
    python tvt_snapshot.py --ip 192.168.1.100 --channel 0 --rtsp-url
    python tvt_snapshot.py --from-json scan_results.json --channel 0 -o ./snapshots/

Stream types
------------
0 = mainstream (full resolution),  1 = substream,  2 = third,  3 = fourth
"""

import argparse
import json
import os
import sys
import textwrap
import urllib.request
import urllib.error
from pathlib import Path

DEFAULT_API_URL = os.getenv("TVT_API_URL", "http://localhost:3000")
DEFAULT_USERNAME = os.getenv("TVT_USERNAME", "admin")
DEFAULT_PASSWORD = os.getenv("TVT_PASSWORD", "admin123")
DEFAULT_PORT = int(os.getenv("TVT_PORT", "9008"))
DEFAULT_TIMEOUT = 30

# Common TVT device ports
PORT_CAMERA = 9008  # IPC cameras
PORT_NVR = 6036  # NVR/DVR devices


def capture_snapshot(
    ip: str,
    channel: int,
    *,
    port: int = DEFAULT_PORT,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    api_url: str = DEFAULT_API_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> bytes | None:
    """Capture a JPEG snapshot from a TVT device channel via an SDK bridge.

    Args:
        ip: Device IP address (NVR or IPC camera)
        channel: Camera channel number (0-based; for IPC cameras, 0 is the camera itself)
        port: Device protocol port (default 6036)
        username: Device login username
        password: Device login password
        api_url: SDK bridge base URL
        timeout: Request timeout in seconds

    Returns:
        JPEG bytes on success, None on failure
    """
    payload = json.dumps(
        {
            "ip": ip,
            "port": port,
            "username": username,
            "password": password,
            "channel": channel,
        }
    ).encode()

    req = urllib.request.Request(
        f"{api_url}/snapshot",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read()

            if "image/jpeg" in content_type:
                return data

            # API returned JSON error
            try:
                err = json.loads(data.decode())
                print(f"  ERROR {ip} ch{channel}: {err.get('error', 'unknown')}", file=sys.stderr)
            except json.JSONDecodeError:
                print(f"  ERROR {ip} ch{channel}: unexpected response", file=sys.stderr)
            return None

    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            err = json.loads(body)
            print(f"  ERROR {ip} ch{channel}: {err.get('error', e.reason)}", file=sys.stderr)
        except json.JSONDecodeError:
            print(f"  ERROR {ip} ch{channel}: HTTP {e.code} {e.reason}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"  ERROR {ip} ch{channel}: API connection error: {e.reason}", file=sys.stderr)
        return None
    except TimeoutError:
        print(f"  ERROR {ip} ch{channel}: timeout after {timeout}s", file=sys.stderr)
        return None


# Stream type constants
STREAM_MAIN = 0
STREAM_SUB = 1
STREAM_THIRD = 2
STREAM_FOURTH = 3

STREAM_TYPE_NAMES = {
    STREAM_MAIN: "mainstream",
    STREAM_SUB: "substream",
    STREAM_THIRD: "third",
    STREAM_FOURTH: "fourth",
}


def get_rtsp_url(
    ip: str,
    channel: int,
    *,
    stream_type: int = STREAM_MAIN,
    port: int = DEFAULT_PORT,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    api_url: str = DEFAULT_API_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> str | None:
    """Get the RTSP URL for a TVT device channel and stream type.

    Args:
        ip: Device IP address (NVR or IPC camera)
        channel: Camera channel number (0-based; for IPC cameras, 0 is the camera itself)
        stream_type: 0=main, 1=sub, 2=third, 3=fourth
        port: Device protocol port (default 6036)
        username: Login username
        password: Login password
        api_url: SDK bridge base URL
        timeout: Request timeout in seconds

    Returns:
        RTSP URL string on success, None on failure
    """
    payload = json.dumps(
        {
            "ip": ip,
            "port": port,
            "username": username,
            "password": password,
            "channel": channel,
            "streamType": stream_type,
        }
    ).encode()

    req = urllib.request.Request(
        f"{api_url}/rtsp-url",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())

            if data.get("success") and data.get("url"):
                return data["url"]

            print(f"  ERROR {ip} ch{channel}: {data.get('error', 'unknown')}", file=sys.stderr)
            return None

    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            err = json.loads(body)
            print(f"  ERROR {ip} ch{channel}: {err.get('error', e.reason)}", file=sys.stderr)
        except json.JSONDecodeError:
            print(f"  ERROR {ip} ch{channel}: HTTP {e.code} {e.reason}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"  ERROR {ip} ch{channel}: API connection error: {e.reason}", file=sys.stderr)
        return None
    except TimeoutError:
        print(f"  ERROR {ip} ch{channel}: timeout after {timeout}s", file=sys.stderr)
        return None


def save_snapshot(
    ip: str,
    channel: int,
    output_path: str,
    **kwargs,
) -> bool:
    """Capture and save a snapshot to a file.

    Args:
        ip: Device IP address (NVR or IPC camera)
        channel: Camera channel number (0-based)
        output_path: Output file path (.jpg)
        **kwargs: Passed to capture_snapshot()

    Returns:
        True if saved successfully
    """
    jpeg = capture_snapshot(ip, channel, **kwargs)
    if jpeg is None:
        return False

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(jpeg)
    return True


def snapshot_all_channels(
    ip: str,
    output_dir: str,
    *,
    max_channels: int = 64,
    port: int = DEFAULT_PORT,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    api_url: str = DEFAULT_API_URL,
    timeout: int = DEFAULT_TIMEOUT,
    site_name: str = "",
) -> list[dict]:
    """Capture snapshots from all channels on a device.

    Scans the device first to get the camera list (NVR returns
    IPC channels; for standalone IPC cameras, channel 0 is the
    camera itself), then captures a snapshot from each online channel.

    Returns:
        List of {channel, name, file, success} dicts
    """
    # First scan to get camera list
    scan_payload = json.dumps(
        {
            "ip": ip,
            "port": port,
            "username": username,
            "password": password,
            "maxCameras": max_channels,
        }
    ).encode()

    req = urllib.request.Request(
        f"{api_url}/scan",
        data=scan_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout + 15) as resp:
            scan = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ERROR scanning {ip}: {e}", file=sys.stderr)
        return []

    if not scan.get("success"):
        print(f"  ERROR scanning {ip}: {scan.get('error', 'unknown')}", file=sys.stderr)
        return []

    cameras = scan.get("cameras", [])
    if not cameras:
        print(f"  {ip}: no cameras found", file=sys.stderr)
        return []

    results = []
    prefix = f"{site_name}_" if site_name else ""

    for cam in cameras:
        ch = cam.get("channel", 0)
        name = cam.get("name", f"ch{ch}")
        status = cam.get("status", "")

        safe_name = name.replace("/", "_").replace(" ", "_")
        filename = f"{prefix}{ip}_ch{ch}_{safe_name}.jpg"
        filepath = os.path.join(output_dir, filename)

        if status == "Offline":
            print(f"  {ip} ch{ch} ({name}): OFFLINE — skipped")
            results.append({"channel": ch, "name": name, "file": None, "success": False, "status": "offline"})
            continue

        ok = save_snapshot(
            ip,
            ch,
            filepath,
            port=port,
            username=username,
            password=password,
            api_url=api_url,
            timeout=timeout,
        )

        if ok:
            print(f"  {ip} ch{ch} ({name}): saved → {filepath}")
        else:
            print(f"  {ip} ch{ch} ({name}): FAILED")

        results.append({"channel": ch, "name": name, "file": filepath if ok else None, "success": ok, "status": status})

    return results


def snapshot_from_json(
    json_path: str,
    channel: int | None,
    output_dir: str,
    *,
    all_channels: bool = False,
    max_channels: int = 64,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    api_url: str = DEFAULT_API_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Capture snapshots from devices listed in a scanner JSON file.

    Args:
        json_path: Path to scanner results JSON
        channel: Specific channel to capture (None if all_channels=True)
        output_dir: Output directory for snapshots
        all_channels: If True, capture all channels on each device
        max_channels: Max channels when scanning
        username/password: Device credentials
        api_url: SDK bridge URL
        timeout: Request timeout

    Returns:
        Summary dict with counts
    """
    with open(json_path) as f:
        data = json.load(f)

    # Support both flat list and {devices: [...]} formats
    devices = data if isinstance(data, list) else data.get("devices", data.get("results", []))

    total = 0
    success = 0
    failed = 0

    for dev in devices:
        ip = dev.get("ip") or dev.get("nvr_ip")
        if not ip:
            continue

        port = dev.get("port", dev.get("nvr_port", DEFAULT_PORT))
        site = dev.get("site", dev.get("hostname", ""))

        print(f"\n[{ip}] {site}")

        if all_channels:
            results = snapshot_all_channels(
                ip,
                output_dir,
                max_channels=max_channels,
                port=port,
                username=username,
                password=password,
                api_url=api_url,
                timeout=timeout,
                site_name=site,
            )
            for r in results:
                total += 1
                if r["success"]:
                    success += 1
                else:
                    failed += 1
        else:
            ch = channel if channel is not None else 0
            safe_site = site.replace("/", "_").replace(" ", "_") if site else ""
            prefix = f"{safe_site}_" if safe_site else ""
            filename = f"{prefix}{ip}_ch{ch}.jpg"
            filepath = os.path.join(output_dir, filename)

            total += 1
            ok = save_snapshot(
                ip,
                ch,
                filepath,
                port=port,
                username=username,
                password=password,
                api_url=api_url,
                timeout=timeout,
            )
            if ok:
                print(f"  ch{ch}: saved → {filepath}")
                success += 1
            else:
                print(f"  ch{ch}: FAILED")
                failed += 1

    return {"total": total, "success": success, "failed": failed}


# ──────────────────────────────────────────────────────────────────────
# Direct mode: NVR web API + ffmpeg (no Docker container required)
# ──────────────────────────────────────────────────────────────────────


def direct_snapshot_all(
    nvr_ip: str,
    output_dir: str,
    *,
    username: str = "admin",
    password: str = "admin",
    web_port: int = 80,
    stream_type: str = "main",
    timeout: int = 15,
) -> list[dict]:
    """Capture RTSP snapshots from all online NVR channels (direct mode).

    Logs into the NVR web API, queries the channel list, then captures
    each online IPC camera directly via its RTSP stream using ffmpeg.
    Offline channels are skipped and logged.

    Args:
        nvr_ip: NVR IP address.
        output_dir: Directory to write JPEG files (created if missing).
        username: NVR login username.
        password: NVR login password.
        web_port: NVR HTTP port (default 80).
        stream_type: RTSP stream — 'main', 'sub', or 'third'.
        timeout: Per-snapshot ffmpeg timeout in seconds.

    Returns:
        List of dicts with keys: channel, name, ip, file, success, status.
    """
    from .nvr_api import NvrClient, rtsp_snapshot as _rtsp_snap

    os.makedirs(output_dir, exist_ok=True)
    results = []

    with NvrClient(nvr_ip, username, password, port=web_port, timeout=timeout) as nvr:
        nvr.login()
        nvr.ensure_services_enabled()
        channels = nvr.query_channels()

        for ch in channels:
            if not ch.online:
                print(f"  CH{ch.chl_num:>2} ({ch.name}): OFFLINE - skipped")
                results.append(
                    {
                        "channel": ch.chl_num,
                        "name": ch.name,
                        "ip": ch.ip,
                        "file": None,
                        "success": False,
                        "status": "offline",
                    }
                )
                continue

            safe = ch.name.replace("/", "_").replace(" ", "_")
            fname = f"{nvr_ip}_ch{ch.chl_num}_{safe}.jpg"
            filepath = os.path.join(output_dir, fname)

            url = nvr.get_rtsp_url(ch.chl_num, stream_type)
            print(f"  CH{ch.chl_num:>2} ({ch.name}) {ch.ip:<15} ", end="", flush=True)
            ok = _rtsp_snap(url, filepath, timeout=timeout)
            if ok:
                size = os.path.getsize(filepath)
                print(f"OK ({size:,} bytes)")
            else:
                print("FAILED")

            results.append(
                {
                    "channel": ch.chl_num,
                    "name": ch.name,
                    "ip": ch.ip,
                    "file": filepath if ok else None,
                    "success": ok,
                    "status": "online",
                }
            )

    return results


def direct_snapshot_channel(
    nvr_ip: str,
    channel: int,
    output_path: str,
    *,
    username: str = "admin",
    password: str = "admin",
    web_port: int = 80,
    stream_type: str = "main",
    timeout: int = 15,
) -> bool:
    """Capture a single RTSP snapshot from an NVR channel (direct mode).

    Logs into the NVR, resolves the IPC camera IP for the given channel,
    then captures one JPEG frame via ffmpeg.

    Args:
        nvr_ip: NVR IP address.
        channel: Channel number (1-indexed).
        output_path: Output JPEG file path.
        username: NVR login username.
        password: NVR login password.
        web_port: NVR HTTP port (default 80).
        stream_type: RTSP stream — 'main', 'sub', or 'third'.
        timeout: ffmpeg timeout in seconds.

    Returns:
        True if the snapshot was saved successfully.
    """
    from .nvr_api import NvrClient, rtsp_snapshot as _rtsp_snap

    with NvrClient(nvr_ip, username, password, port=web_port, timeout=timeout) as nvr:
        nvr.login()
        nvr.ensure_services_enabled()
        url = nvr.get_rtsp_url(channel, stream_type)
        return _rtsp_snap(url, output_path, timeout=timeout)


def direct_snapshot_from_json(
    json_path: str,
    output_dir: str,
    *,
    channel: int | None = None,
    all_channels: bool = False,
    username: str = "admin",
    password: str = "admin",
    web_port: int = 80,
    stream_type: str = "main",
    timeout: int = 15,
) -> dict:
    """Capture direct RTSP snapshots from NVRs listed in a JSON file.

    Reads a JSON inventory (array of objects or ``{"devices": [...]}``),
    iterates each NVR, and captures snapshots using the NVR web API +
    ffmpeg RTSP.  Each object should have an ``ip`` or ``nvr_ip`` key.

    Args:
        json_path: Path to JSON inventory file.
        output_dir: Directory to write JPEG files.
        channel: Specific channel to capture (None defaults to 1).
        all_channels: If True, capture every online channel per NVR.
        username: NVR login username.
        password: NVR login password.
        web_port: NVR HTTP port (default 80).
        stream_type: RTSP stream — 'main', 'sub', or 'third'.
        timeout: Per-snapshot ffmpeg timeout in seconds.

    Returns:
        Dict with keys: total, success, failed.
    """
    from .nvr_api import NvrClient, rtsp_snapshot as _rtsp_snap

    with open(json_path) as f:
        data = json.load(f)

    devices = data if isinstance(data, list) else data.get("devices", data.get("results", []))

    total = 0
    success = 0
    failed = 0

    for dev in devices:
        ip = dev.get("ip") or dev.get("nvr_ip")
        if not ip:
            continue

        site = dev.get("site", dev.get("hostname", ""))
        print(f"\n[{ip}] {site}")

        if all_channels:
            results = direct_snapshot_all(
                ip,
                output_dir,
                username=username,
                password=password,
                web_port=web_port,
                stream_type=stream_type,
                timeout=timeout,
            )
            for r in results:
                total += 1
                if r["success"]:
                    success += 1
                else:
                    failed += 1
        else:
            ch = channel if channel is not None else 1
            safe_site = site.replace("/", "_").replace(" ", "_") if site else ""
            prefix = f"{safe_site}_" if safe_site else ""
            filepath = os.path.join(output_dir, f"{prefix}{ip}_ch{ch}.jpg")

            total += 1
            ok = direct_snapshot_channel(
                ip,
                ch,
                filepath,
                username=username,
                password=password,
                web_port=web_port,
                stream_type=stream_type,
                timeout=timeout,
            )
            if ok:
                print(f"  ch{ch}: saved -> {filepath}")
                success += 1
            else:
                print(f"  ch{ch}: FAILED")
                failed += 1

    return {"total": total, "success": success, "failed": failed}


def main():
    parser = argparse.ArgumentParser(
        description="Capture JPEG snapshots or RTSP URLs from TVT devices (NVR/IPC)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            modes:
              Default (SDK):   Uses an SDK bridge service on port 9008/6036
              --direct:        Uses NVR web API (port 80) + ffmpeg RTSP — no Docker needed

            examples:
              # Direct mode — all channels from NVR
              %(prog)s --direct --ip 192.168.1.100 --all-channels -o ./snapshots

              # Direct mode — single channel
              %(prog)s --direct --ip 192.168.1.100 -c 3 -o ch3.jpg

              # Direct mode — from JSON file
              %(prog)s --direct --from-json sites.json --all-channels -o ./snapshots

              # SDK mode (default) — all channels
              %(prog)s --ip 192.168.1.100 --all-channels -o ./snapshots
        """),
    )

    # Mode selection
    parser.add_argument(
        "--direct", action="store_true", help="Use NVR web API + ffmpeg RTSP directly (no SDK bridge service)"
    )

    # Target selection
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--ip", help="Device IP address (NVR or IPC camera)")
    target.add_argument("--from-json", metavar="JSON_FILE", help="Scanner results JSON file")

    # Channel selection
    ch_group = parser.add_mutually_exclusive_group()
    ch_group.add_argument(
        "--channel",
        "-c",
        type=int,
        default=0,
        help="Channel number (default: 0; for IPC cameras, 0 is the camera itself)",
    )
    ch_group.add_argument("--all-channels", action="store_true", help="Capture all channels on the device")

    # RTSP URL mode (SDK only)
    parser.add_argument(
        "--rtsp-url", action="store_true", help="Retrieve RTSP URL instead of capturing a snapshot (SDK mode only)"
    )
    parser.add_argument(
        "--stream-type",
        type=int,
        default=0,
        choices=[0, 1, 2, 3],
        help="Stream type: 0=main, 1=sub, 2=third, 3=fourth (default: 0)",
    )

    # Output
    parser.add_argument("-o", "--output", help="Output file (.jpg) or directory (with --all-channels or --from-json)")

    # Connection
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Device protocol port (default: {DEFAULT_PORT}; cameras=9008, NVRs=6036)",
    )
    parser.add_argument(
        "--web-port", type=int, default=80, help="NVR web interface port for --direct mode (default: 80)"
    )
    parser.add_argument(
        "--username", "-u", default=DEFAULT_USERNAME, help=f"Device username (default: {DEFAULT_USERNAME})"
    )
    parser.add_argument("--password", "-p", default=DEFAULT_PASSWORD, help="Device password")
    parser.add_argument(
        "--api-url", default=DEFAULT_API_URL, help=f"SDK bridge URL for SDK mode (default: {DEFAULT_API_URL})"
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"Request timeout seconds (default: {DEFAULT_TIMEOUT})"
    )
    parser.add_argument(
        "--max-channels", type=int, default=64, help="Max channels when scanning in SDK mode (default: 64)"
    )

    args = parser.parse_args()

    # Validate
    if not args.rtsp_url and not args.output:
        parser.error("--output / -o is required for snapshot mode (omit only with --rtsp-url)")
    if args.direct and args.rtsp_url:
        parser.error("--rtsp-url is only supported in SDK mode (remove --direct)")

    # Map stream-type int to name for direct mode
    stream_map = {0: "main", 1: "sub", 2: "third", 3: "fourth"}
    stream_name = stream_map.get(args.stream_type, "main")

    # ── Direct mode ──────────────────────────────────────────────────
    if args.direct:
        if args.ip:
            if args.all_channels:
                print(f"[direct] Capturing all channels from {args.ip} → {args.output}/")
                results = direct_snapshot_all(
                    args.ip,
                    args.output,
                    username=args.username,
                    password=args.password,
                    web_port=args.web_port,
                    stream_type=stream_name,
                    timeout=args.timeout,
                )
                ok = sum(1 for r in results if r["success"])
                total = len(results)
                print(f"\nDone: {ok}/{total} snapshots captured")
                if ok < total:
                    sys.exit(1)
            else:
                ch = args.channel if args.channel != 0 else 1
                output_file = args.output
                print(f"[direct] Capturing {args.ip} ch{ch} → {output_file}")
                ok = direct_snapshot_channel(
                    args.ip,
                    ch,
                    output_file,
                    username=args.username,
                    password=args.password,
                    web_port=args.web_port,
                    stream_type=stream_name,
                    timeout=args.timeout,
                )
                if ok:
                    size = os.path.getsize(output_file)
                    print(f"Saved: {output_file} ({size:,} bytes)")
                else:
                    print("FAILED", file=sys.stderr)
                    sys.exit(1)

        elif args.from_json:
            print(f"[direct] Capturing from {args.from_json} → {args.output}/")
            summary = direct_snapshot_from_json(
                args.from_json,
                args.output,
                channel=args.channel if not args.all_channels else None,
                all_channels=args.all_channels,
                username=args.username,
                password=args.password,
                web_port=args.web_port,
                stream_type=stream_name,
                timeout=args.timeout,
            )
            print(f"\nDone: {summary['success']}/{summary['total']} snapshots captured, {summary['failed']} failed")
            if summary["failed"] > 0:
                sys.exit(1)
        return

    # ── SDK mode (default) ───────────────────────────────────────────
    if args.ip:
        if args.rtsp_url:
            sname = STREAM_TYPE_NAMES.get(args.stream_type, str(args.stream_type))
            print(f"Getting RTSP URL for {args.ip} ch{args.channel} ({sname})...")
            url = get_rtsp_url(
                args.ip,
                args.channel,
                stream_type=args.stream_type,
                port=args.port,
                username=args.username,
                password=args.password,
                api_url=args.api_url,
                timeout=args.timeout,
            )
            if url:
                print(url)
            else:
                print("FAILED", file=sys.stderr)
                sys.exit(1)
        elif args.all_channels:
            output_dir = args.output
            print(f"Capturing all channels from {args.ip} → {output_dir}/")
            results = snapshot_all_channels(
                args.ip,
                output_dir,
                max_channels=args.max_channels,
                port=args.port,
                username=args.username,
                password=args.password,
                api_url=args.api_url,
                timeout=args.timeout,
            )
            ok = sum(1 for r in results if r["success"])
            total = len(results)
            print(f"\nDone: {ok}/{total} snapshots captured")
        else:
            output_file = args.output
            print(f"Capturing {args.ip} ch{args.channel} → {output_file}")
            ok = save_snapshot(
                args.ip,
                args.channel,
                output_file,
                port=args.port,
                username=args.username,
                password=args.password,
                api_url=args.api_url,
                timeout=args.timeout,
            )
            if ok:
                size = os.path.getsize(output_file)
                print(f"Saved: {output_file} ({size:,} bytes)")
            else:
                print("FAILED", file=sys.stderr)
                sys.exit(1)

    elif args.from_json:
        output_dir = args.output
        print(f"Capturing snapshots from {args.from_json} → {output_dir}/")
        summary = snapshot_from_json(
            args.from_json,
            channel=args.channel if not args.all_channels else None,
            output_dir=output_dir,
            all_channels=args.all_channels,
            max_channels=args.max_channels,
            username=args.username,
            password=args.password,
            api_url=args.api_url,
            timeout=args.timeout,
        )
        print(f"\nDone: {summary['success']}/{summary['total']} snapshots captured, {summary['failed']} failed")
        if summary["failed"] > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
