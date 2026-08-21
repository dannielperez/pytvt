"""The snapshot benchmark harness must isolate a per-leg failure.

The tool runs several capture legs (webapi / rtsp / netsdk) in one invocation.
A leg's *setup* can raise before any capture loop runs — an SDK that will not
load, a recorder login that throws, a bare IPC with no web CGI. That exception
used to propagate out of ``main`` and abort the whole run, discarding the
results already collected for the legs that succeeded.

These tests pin the isolation: one leg raising leaves the others' results
intact and surfaces as a failed leg, not a crash. They also pin the rtsp
leg's direct-IPC fallback. No network: the leg functions are stubbed.
"""

from __future__ import annotations

import importlib.util
import os
import sys

_SPEC = importlib.util.spec_from_file_location(
    "snapshot_benchmark",
    os.path.join(os.path.dirname(__file__), "..", "tools", "snapshot_benchmark.py"),
)
bench = importlib.util.module_from_spec(_SPEC)
# Register before exec so @dataclass can resolve the module by its __module__.
sys.modules["snapshot_benchmark"] = bench
_SPEC.loader.exec_module(bench)


def test_leg_setup_failure_does_not_abort_the_run(monkeypatch, capsys):
    ok = bench.LegReport(leg="webapi", ok=1, latencies_ms=[12.0], sizes=[2048], width=352, height=240)

    def boom(_args):
        raise RuntimeError("SDK failed to load")

    # main() builds its leg map from these module globals at call time.
    monkeypatch.setattr(bench, "bench_webapi", lambda _a: ok)
    monkeypatch.setattr(bench, "bench_netsdk", boom)
    monkeypatch.setattr(
        bench.sys,
        "argv",
        ["snapshot_benchmark.py", "--ip", "10.0.0.1", "-p", "pw", "-n", "1", "--legs", "webapi,netsdk"],
    )

    rc = bench.main()
    out = capsys.readouterr().out

    assert rc == 0
    # The good leg's result survived the other leg's crash.
    assert "webapi" in out and "352x240" in out
    # The failing leg is reported as failed, with its reason, not a traceback.
    assert "netsdk" in out
    assert "SDK failed to load" in out


def test_rtsp_falls_back_to_direct_ipc_url_when_cgi_login_fails(monkeypatch):
    captured = {}

    class _FailingNvr:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self):
            raise RuntimeError("no nonce in response")

    class _Frame:
        def __init__(self, image):
            self.image = image
            self.error = None

    def fake_grab(url, timeout):
        captured["url"] = url
        return _Frame(image=b"\xff\xd8\xff\xe0" + b"\x00" * 40)

    import pytvt.xml_api as xml_api

    monkeypatch.setattr(xml_api, "NvrClient", _FailingNvr)
    monkeypatch.setattr(xml_api, "rtsp_snapshot_attempt_bytes", fake_grab)

    args = type(
        "A",
        (),
        dict(
            ip="10.0.0.2",
            username="ad min",
            password="p@ss/word",
            web_port=80,
            rtsp_port=554,
            channel=1,
            timeout=5,
            iterations=1,
        ),
    )()
    report = bench.bench_rtsp(args)

    assert report.ok == 1
    # Credentials are URL-encoded and the direct profile1 URL is used.
    assert captured["url"] == "rtsp://ad%20min:p%40ss%2Fword@10.0.0.2:554/profile1"
    assert any("direct RTSP" in e for e in report.errors)
