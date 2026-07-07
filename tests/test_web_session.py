"""Tests for pytvt.platform_sdk.web_session + web_envelope (TVT-2).

No network: a fake transport replays sanitized reqLogin/doLogin envelopes
captured from the documented protocol (docs/ai knowledge spec). Covers the
handshake form fields, token/cookie decoration, heartbeat, 408 re-auth,
failure mapping, per-request timeout, and the no-plaintext-password rule.
"""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import pytest

from pytvt.platform_sdk import web_crypto
from pytvt.platform_sdk.exceptions import (
    ManagementAuthError,
    ManagementNotAuthenticatedError,
    ProtocolError,
    SessionExpired,
    TransportError,
)
from pytvt.platform_sdk.web_envelope import parse_envelope
from pytvt.platform_sdk.web_session import (
    DO_LOGIN_PATH,
    HEARTBEAT_PATH,
    REQ_LOGIN_PATH,
    UrllibTransport,
    WebHttpResponse,
    WebSession,
)

USERNAME = "operator"
PASSWORD = "hunter2"
NONCE = "ABCDEFGHIJKLMNOPQRST"  # >= 16 chars, prefix is the AES key
REAL_AUTH_ID = "auth-9f3"


# ── fixtures / fakes ─────────────────────────────────────────────────


def envelope_body(result_xml: str, ret_code: str = "1", ret_msg: str = "success") -> bytes:
    return json.dumps({"retCode": ret_code, "retMsg": ret_msg, "resultXml": result_xml}).encode("utf-8")


def req_login_xml(nonce: str = NONCE, session_id: str = "{SESS-1}", token: str = "tok-1") -> str:
    return (
        "<response><status>success</status><content>"
        f"<nonce>{nonce}</nonce><sessionId>{session_id}</sessionId><token>{token}</token>"
        "</content></response>"
    )


def do_login_xml() -> str:
    encrypted = web_crypto.aes_ecb_pkcs7_encrypt(REAL_AUTH_ID, web_crypto._md5_hex(PASSWORD)[:16])
    return (
        "<response><status>success</status><content>"
        f"<type>admin</type><authId>{encrypted}</authId><userId>7</userId><showEmap>false</showEmap>"
        "</content></response>"
    )


def fail_xml(error_code: str) -> str:
    return f"<response><status>fail</status><errorCode>{error_code}</errorCode></response>"


def ok_response(result_xml: str, *, set_cookie: str | None = None) -> WebHttpResponse:
    headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),)
    if set_cookie is not None:
        headers = (*headers, ("Set-Cookie", set_cookie))
    return WebHttpResponse(status=200, headers=headers, body=envelope_body(result_xml))


@dataclass
class RecordedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout: float

    @property
    def path(self) -> str:
        return urllib.parse.urlsplit(self.url).path

    @property
    def form(self) -> dict[str, str]:
        parsed = urllib.parse.parse_qs((self.body or b"").decode("utf-8"), keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}


@dataclass
class FakeTransport:
    """Replays a scripted list of responses (or raises scripted exceptions)."""

    script: list[WebHttpResponse | Exception] = field(default_factory=list)
    requests: list[RecordedRequest] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> WebHttpResponse:
        self.requests.append(RecordedRequest(method, url, dict(headers), body, timeout))
        if not self.script:
            raise AssertionError(f"unexpected request beyond script: {url}")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def logged_in_session(
    extra_script: list[WebHttpResponse | Exception] | None = None,
    **session_kwargs: object,
) -> tuple[WebSession, FakeTransport]:
    transport = FakeTransport(
        script=[
            ok_response(req_login_xml()),
            ok_response(do_login_xml(), set_cookie="JSESSIONID=abc123; Path=/; HttpOnly"),
            *(extra_script or []),
        ]
    )
    session = WebSession("nvms.example", USERNAME, PASSWORD, transport=transport, **session_kwargs)  # type: ignore[arg-type]
    session.login()
    return session, transport


# ── envelope parsing ─────────────────────────────────────────────────


class TestParseEnvelope:
    def test_success_with_scalars_and_items(self):
        xml = (
            "<response><status>success</status><content>"
            "<total>2</total>"
            "<item><name>srv-a</name><ip>10.0.0.1</ip></item>"
            "<item><name>srv-b</name><ip>10.0.0.2</ip></item>"
            "</content></response>"
        )
        env = parse_envelope(envelope_body(xml))
        assert env.ok
        assert env.ret_code == "1"
        assert env.content == {"total": "2"}
        assert env.items == [
            {"name": "srv-a", "ip": "10.0.0.1"},
            {"name": "srv-b", "ip": "10.0.0.2"},
        ]

    def test_fail_status_surfaces_error_code_without_raising(self):
        env = parse_envelope(envelope_body(fail_xml("536870947")))
        assert not env.ok
        assert env.status == "fail"
        assert env.error_code == "536870947"

    def test_transport_ret_code_not_ok_raises(self):
        with pytest.raises(ProtocolError, match="retCode='0'"):
            parse_envelope(envelope_body("<response/>", ret_code="0", ret_msg="boom"))

    def test_non_json_raises(self):
        with pytest.raises(ProtocolError, match="not JSON"):
            parse_envelope(b"<html>tomcat error page</html>")

    def test_missing_result_xml_raises(self):
        with pytest.raises(ProtocolError, match="no resultXml"):
            parse_envelope(json.dumps({"retCode": "1", "retMsg": "ok"}).encode())

    def test_invalid_xml_raises(self):
        with pytest.raises(ProtocolError, match="not valid XML"):
            parse_envelope(envelope_body("<response><unclosed>"))


# ── crypto md5-variants (session re-auth support) ────────────────────


class TestCryptoMd5Variants:
    def test_login_password_hash_from_md5_matches_plaintext_path(self):
        md5 = web_crypto._md5_hex(PASSWORD)
        assert web_crypto.login_password_hash_from_md5(md5, NONCE) == web_crypto.login_password_hash(PASSWORD, NONCE)

    def test_decrypt_auth_id_from_md5_matches_plaintext_path(self):
        md5 = web_crypto._md5_hex(PASSWORD)
        token = web_crypto.aes_ecb_pkcs7_encrypt(REAL_AUTH_ID, md5[:16])
        assert web_crypto.decrypt_auth_id_from_md5(token, md5) == REAL_AUTH_ID
        assert web_crypto.decrypt_auth_id(token, PASSWORD) == REAL_AUTH_ID


# ── login handshake ──────────────────────────────────────────────────


class TestLogin:
    def test_two_step_handshake_form_fields(self):
        session, transport = logged_in_session()
        assert [r.path for r in transport.requests] == [REQ_LOGIN_PATH, DO_LOGIN_PATH]
        assert all(r.method == "POST" for r in transport.requests)

        do_login = transport.requests[1].form
        # userName is AES-ECB-PKCS7 under nonce[:16]
        assert web_crypto.aes_ecb_pkcs7_decrypt(do_login["userName"], NONCE[:16]) == USERNAME
        # password is sha256(md5(pw) + nonce)
        assert do_login["password"] == web_crypto.login_password_hash(PASSWORD, NONCE)
        # sessionId braces are stripped; token form field is empty by protocol
        assert do_login["sessionId"] == "SESS-1"
        assert do_login["token"] == ""
        assert session.authenticated

    def test_login_decrypts_auth_id_and_stores_user_id(self):
        session, _ = logged_in_session()
        assert session.auth_id == REAL_AUTH_ID
        assert session.user_id == "7"

    def test_rejected_credentials_raise_auth_error_with_hint(self):
        transport = FakeTransport(script=[ok_response(req_login_xml()), ok_response(fail_xml("536870947"))])
        session = WebSession("nvms.example", USERNAME, "wrong", transport=transport)
        with pytest.raises(ManagementAuthError, match="invalid username or password"):
            session.login()
        assert not session.authenticated

    def test_locked_account_hint(self):
        transport = FakeTransport(script=[ok_response(req_login_xml()), ok_response(fail_xml("536870951"))])
        session = WebSession("nvms.example", USERNAME, PASSWORD, transport=transport)
        with pytest.raises(ManagementAuthError, match="account locked"):
            session.login()

    def test_req_login_missing_nonce_is_protocol_error(self):
        xml = "<response><status>success</status><content><token>tok-1</token></content></response>"
        transport = FakeTransport(script=[ok_response(xml)])
        session = WebSession("nvms.example", USERNAME, PASSWORD, transport=transport)
        with pytest.raises(ProtocolError, match="missing nonce or token"):
            session.login()

    def test_plaintext_password_never_retained(self):
        session, _ = logged_in_session()
        state = repr(vars(session))
        assert PASSWORD not in state
        # the digest the protocol mandates IS retained (re-auth needs it)
        assert web_crypto._md5_hex(PASSWORD) in state


# ── authenticated requests ───────────────────────────────────────────


class TestAuthenticatedRequest:
    def test_request_before_login_raises(self):
        session = WebSession("nvms.example", USERNAME, PASSWORD, transport=FakeTransport())
        with pytest.raises(ManagementNotAuthenticatedError):
            session.request("/service/Alarm/getAlarmInfoList")

    def test_request_attaches_token_header_and_session_cookie(self):
        ok = ok_response("<response><status>success</status><content/></response>")
        session, transport = logged_in_session(extra_script=[ok])
        env = session.request("/service/SystemStatus/getServerStatusList")
        assert env.ok
        sent = transport.requests[-1]
        assert sent.headers["token"] == "tok-1"
        assert "JSESSIONID=abc123" in sent.headers["Cookie"]

    def test_every_request_carries_the_configured_timeout(self):
        ok = ok_response("<response><status>success</status><content/></response>")
        session, transport = logged_in_session(extra_script=[ok], timeout=7.5)
        session.request("/service/SystemStatus/getServerStatusList")
        assert [r.timeout for r in transport.requests] == [7.5, 7.5, 7.5]

    def test_heartbeat_posts_to_heartbeat_path(self):
        ok = ok_response("<response><status>success</status><content/></response>")
        session, transport = logged_in_session(extra_script=[ok])
        session.heartbeat()
        assert transport.requests[-1].path == HEARTBEAT_PATH

    def test_http_408_reauthenticates_once_and_retries(self):
        expired = WebHttpResponse(status=408, headers=(), body=b"")
        ok = ok_response("<response><status>success</status><content/></response>")
        session, transport = logged_in_session(
            extra_script=[
                expired,
                ok_response(req_login_xml(token="tok-2"), set_cookie="JSESSIONID=def456; Path=/"),
                ok_response(do_login_xml()),
                ok,
            ]
        )
        env = session.request("/service/Alarm/getAlarmInfoList")
        assert env.ok
        paths = [r.path for r in transport.requests]
        assert paths == [
            REQ_LOGIN_PATH,
            DO_LOGIN_PATH,
            "/service/Alarm/getAlarmInfoList",
            REQ_LOGIN_PATH,
            DO_LOGIN_PATH,
            "/service/Alarm/getAlarmInfoList",
        ]
        retried = transport.requests[-1]
        assert retried.headers["token"] == "tok-2"
        assert "JSESSIONID=def456" in retried.headers["Cookie"]

    def test_408_after_reauth_raises_session_expired(self):
        expired = WebHttpResponse(status=408, headers=(), body=b"")
        session, _ = logged_in_session(
            extra_script=[
                expired,
                ok_response(req_login_xml(token="tok-2")),
                ok_response(do_login_xml()),
                expired,
            ]
        )
        with pytest.raises(SessionExpired):
            session.request("/service/Alarm/getAlarmInfoList")

    def test_unexpected_http_status_is_transport_error(self):
        boom = WebHttpResponse(status=500, headers=(), body=b"oops")
        session, _ = logged_in_session(extra_script=[boom])
        with pytest.raises(TransportError, match="HTTP 500"):
            session.request("/service/Alarm/getAlarmInfoList")

    def test_transport_error_propagates(self):
        session, transport = logged_in_session()
        transport.script.append(TransportError("connect timed out"))
        with pytest.raises(TransportError, match="timed out"):
            session.request("/service/Alarm/getAlarmInfoList")

    def test_close_forgets_session_state(self):
        session, _ = logged_in_session()
        session.close()
        assert not session.authenticated
        assert session.auth_id is None
        with pytest.raises(ManagementNotAuthenticatedError):
            session.request("/service/Alarm/getAlarmInfoList")


# ── default transport (timeout path, no network) ─────────────────────


class TestUrllibTransport:
    def test_oserror_becomes_transport_error(self, monkeypatch):
        def raise_timeout(req, timeout):
            raise OSError("timed out")

        monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)
        with pytest.raises(TransportError, match="timed out"):
            UrllibTransport().request(
                "POST",
                "http://nvms.example:80/service/login/reqLogin",
                headers={},
                body=b"",
                timeout=0.01,
            )

    def test_rejects_bad_scheme_via_session_ctor(self):
        with pytest.raises(ValueError, match="scheme"):
            WebSession("nvms.example", USERNAME, PASSWORD, scheme="ftp")

    def test_https_scheme_selected_in_request_url(self, monkeypatch):
        seen_urls = []

        def capture(req, **kwargs):
            seen_urls.append(req.full_url)
            raise OSError("stop after capture")

        monkeypatch.setattr(urllib.request, "urlopen", capture)
        session = WebSession("nvms.example", USERNAME, PASSWORD, scheme="https")
        with pytest.raises(TransportError):
            session.login()
        assert seen_urls and seen_urls[0].startswith("https://nvms.example:443/")

    def test_verify_tls_true_passes_no_ssl_context(self, monkeypatch):
        seen_kwargs = []

        def capture(req, **kwargs):
            seen_kwargs.append(kwargs)
            raise OSError("stop after capture")

        monkeypatch.setattr(urllib.request, "urlopen", capture)
        with pytest.raises(TransportError):
            UrllibTransport(verify_tls=True).request(
                "POST", "https://nvms.example:443/service/login/reqLogin", headers={}, body=b"", timeout=1.0
            )
        assert "context" not in seen_kwargs[0]

    def test_verify_tls_false_passes_unverified_ssl_context(self, monkeypatch):
        seen_kwargs = []

        def capture(req, **kwargs):
            seen_kwargs.append(kwargs)
            raise OSError("stop after capture")

        monkeypatch.setattr(urllib.request, "urlopen", capture)
        with pytest.raises(TransportError):
            UrllibTransport(verify_tls=False).request(
                "POST", "https://nvms.example:443/service/login/reqLogin", headers={}, body=b"", timeout=1.0
            )
        context = seen_kwargs[0]["context"]
        assert context.verify_mode == ssl.CERT_NONE
        assert context.check_hostname is False
