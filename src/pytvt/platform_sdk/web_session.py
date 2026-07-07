"""Authenticated session for the TVT NVMS management web API (``/service/*``).

Implements the documented two-step nonce/hash handshake
(``reqLogin`` → compute → ``doLogin``), then decorates every request with the
``token`` header plus the Tomcat ``JSESSIONID`` cookie, keeps the session
alive via ``heartBeat``, and transparently re-authenticates once when the
server answers HTTP 408 (session expired).

Security posture: the plaintext password is used transiently during
construction and **never stored on the instance** — only the vendor-mandated
``md5_hex(password)`` digest is kept, which is what re-authentication and
``authId`` decryption actually require. Read-only consumption (R3): this
layer issues requests; it encodes no mutation endpoints.

The HTTP transport is injectable (:class:`WebTransport`) so tests replay
sanitized envelopes with no network; the default transport is stdlib
``urllib`` with a per-request timeout.
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from pytvt.platform_sdk import web_crypto
from pytvt.platform_sdk.exceptions import (
    ManagementAuthError,
    ManagementNotAuthenticatedError,
    ProtocolError,
    SessionExpired,
    TransportError,
)
from pytvt.platform_sdk.web_envelope import WebEnvelope, parse_envelope

DEFAULT_TIMEOUT = 10.0

_HTTP_SESSION_EXPIRED = 408
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"

# Login errorCode → operator-facing hint (from the shipped NVMS web client).
_LOGIN_ERROR_HINTS = {
    "536870947": "invalid username or password",
    "536870948": "invalid username or password",
    "536870951": "account locked",
}

REQ_LOGIN_PATH = "/service/login/reqLogin"
DO_LOGIN_PATH = "/service/login/doLogin"
HEARTBEAT_PATH = "/service/login/heartBeat"


@dataclass(frozen=True)
class WebHttpResponse:
    """Minimal HTTP response surface a transport must produce.

    Attributes:
        status: HTTP status code.
        headers: All response headers as (name, value) pairs — a tuple of
            pairs (not a dict) so repeated headers such as ``Set-Cookie``
            survive intact.
        body: Raw response body bytes.
    """

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def headers_named(self, name: str) -> list[str]:
        """Return every value of a (case-insensitive) header name."""
        wanted = name.lower()
        return [value for key, value in self.headers if key.lower() == wanted]


class WebTransport(Protocol):
    """Injectable HTTP transport for :class:`WebSession`."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> WebHttpResponse:
        """Perform one HTTP request and return the raw response.

        Raises:
            TransportError: On any network-level failure (DNS, connect,
                timeout, TLS, connection reset).
        """
        ...  # pragma: no cover - Protocol signature


class UrllibTransport:
    """Default stdlib transport (``urllib``), one request per call.

    Args:
        verify_tls: When False, builds an unverified SSL context (no cert/
            hostname check) for ``https`` requests — many NVR management
            web UIs ship a self-signed cert with no way to install a trusted
            CA. Ignored for ``http`` requests. Defaults to True (verify).
    """

    def __init__(self, *, verify_tls: bool = True) -> None:
        self._ssl_context = None if verify_tls else ssl._create_unverified_context()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> WebHttpResponse:
        req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with self._open(req, timeout) as resp:
                return WebHttpResponse(
                    status=resp.status,
                    headers=tuple((k, v) for k, v in resp.getheaders()),
                    body=resp.read(),
                )
        except urllib.error.HTTPError as exc:
            # Non-2xx still carries a response (408 handling needs it).
            with exc:
                return WebHttpResponse(
                    status=exc.code,
                    headers=tuple((k, v) for k, v in (exc.headers or {}).items()),
                    body=exc.read() or b"",
                )
        except OSError as exc:  # URLError, socket.timeout, ConnectionError
            raise TransportError(f"management web request to {url} failed: {exc}") from exc

    def _open(self, req: urllib.request.Request, timeout: float):
        if self._ssl_context is not None:
            return urllib.request.urlopen(req, timeout=timeout, context=self._ssl_context)
        return urllib.request.urlopen(req, timeout=timeout)


class WebSession:
    """Authenticated ``/service/*`` session (handshake, token+cookie, re-auth).

    Args:
        host: Management-server hostname or IP.
        username: Login user.
        password: Login password — used transiently, never stored.
        scheme: ``http`` or ``https``.
        port: TCP port; defaults to the scheme's standard port.
        timeout: Per-request timeout in seconds (every request carries it).
        verify_tls: When False, the default transport skips TLS cert/hostname
            verification (self-signed NVR certs are common). Ignored when an
            explicit ``transport`` is supplied — the caller owns that choice.
        transport: Optional :class:`WebTransport`; defaults to urllib.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        scheme: str = "http",
        port: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        verify_tls: bool = True,
        transport: WebTransport | None = None,
    ) -> None:
        if scheme not in ("http", "https"):
            raise ValueError(f"scheme must be http or https, got {scheme!r}")
        self._host = host
        self._scheme = scheme
        self._port = port if port is not None else (443 if scheme == "https" else 80)
        self._timeout = float(timeout)
        self._transport: WebTransport = transport if transport is not None else UrllibTransport(verify_tls=verify_tls)
        self._username = username
        # Vendor protocol derives everything re-auth needs from md5_hex(password);
        # keep only that digest so the plaintext never lives on the instance.
        self._password_md5 = web_crypto._md5_hex(password)
        self._token: str | None = None
        self._auth_id: str | None = None
        self._user_id: str | None = None
        self._cookies: dict[str, str] = {}

    # ── public surface ────────────────────────────────────────────────

    @property
    def authenticated(self) -> bool:
        """True once the two-step handshake has completed."""
        return self._token is not None

    @property
    def auth_id(self) -> str | None:
        """The decrypted server-issued auth id (post-login), if provided."""
        return self._auth_id

    @property
    def user_id(self) -> str | None:
        """The server-issued user id (post-login), if provided."""
        return self._user_id

    def login(self) -> None:
        """Run the two-step handshake and store token + session cookie.

        Raises:
            ManagementAuthError: When the server rejects the credentials.
            ProtocolError: When a handshake response violates the envelope.
            TransportError: On network failure.
        """
        req_env = self._exchange(REQ_LOGIN_PATH, form=None)
        if not req_env.ok:
            raise ManagementAuthError(f"reqLogin failed: status={req_env.status!r} errorCode={req_env.error_code!r}")
        nonce = req_env.content.get("nonce", "")
        session_id = req_env.content.get("sessionId", "").strip("{}")
        token = req_env.content.get("token", "")
        if not nonce or not token:
            raise ProtocolError("reqLogin response is missing nonce or token")

        form = {
            "userName": web_crypto.encrypt_username(self._username, nonce),
            "password": web_crypto.login_password_hash_from_md5(self._password_md5, nonce),
            "token": "",
            "sessionId": session_id,
        }
        do_env = self._exchange(DO_LOGIN_PATH, form=form)
        if not do_env.ok:
            hint = _LOGIN_ERROR_HINTS.get(do_env.error_code or "", "login rejected")
            raise ManagementAuthError(f"doLogin failed: {hint} (errorCode={do_env.error_code!r})")

        self._token = token
        encrypted_auth_id = do_env.content.get("authId", "")
        self._auth_id = (
            web_crypto.decrypt_auth_id_from_md5(encrypted_auth_id, self._password_md5) if encrypted_auth_id else None
        )
        self._user_id = do_env.content.get("userId") or None

    def request(self, path: str, form: dict[str, str] | None = None) -> WebEnvelope:
        """POST an authenticated ``/service/*`` request and parse the envelope.

        On HTTP 408 (session expired) the session re-runs the handshake once
        and retries the request once.

        Raises:
            ManagementNotAuthenticatedError: If called before :meth:`login`.
            SessionExpired: If the retry after re-authentication still gets 408.
            ProtocolError: On an envelope violation.
            TransportError: On network failure or an unexpected HTTP status.
        """
        if not self.authenticated:
            raise ManagementNotAuthenticatedError("call login() before issuing requests")
        response = self._post(path, form)
        if response.status == _HTTP_SESSION_EXPIRED:
            self._reset()
            self.login()
            response = self._post(path, form)
            if response.status == _HTTP_SESSION_EXPIRED:
                raise SessionExpired(f"session still expired after re-authentication ({path})")
        return self._parse_http(path, response)

    def heartbeat(self) -> WebEnvelope:
        """Keep the session alive (``/service/login/heartBeat``)."""
        return self.request(HEARTBEAT_PATH)

    def close(self) -> None:
        """Forget the session state (token, cookies, ids). No network call."""
        self._reset()

    # ── internals ─────────────────────────────────────────────────────

    def _reset(self) -> None:
        self._token = None
        self._auth_id = None
        self._user_id = None
        self._cookies.clear()

    def _url(self, path: str) -> str:
        return f"{self._scheme}://{self._host}:{self._port}{path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": _FORM_CONTENT_TYPE}
        if self._token is not None:
            headers["token"] = self._token
        if self._cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        return headers

    def _post(self, path: str, form: dict[str, str] | None) -> WebHttpResponse:
        body = urllib.parse.urlencode(form).encode("utf-8") if form else b""
        response = self._transport.request(
            "POST",
            self._url(path),
            headers=self._headers(),
            body=body,
            timeout=self._timeout,
        )
        self._capture_cookies(response)
        return response

    def _capture_cookies(self, response: WebHttpResponse) -> None:
        for raw in response.headers_named("Set-Cookie"):
            first = raw.split(";", 1)[0]
            name, sep, value = first.partition("=")
            if sep and name.strip():
                self._cookies[name.strip()] = value.strip()

    def _exchange(self, path: str, form: dict[str, str] | None) -> WebEnvelope:
        """POST (unauthenticated-capable) and parse; no 408 retry here."""
        return self._parse_http(path, self._post(path, form))

    @staticmethod
    def _parse_http(path: str, response: WebHttpResponse) -> WebEnvelope:
        if response.status != 200:
            raise TransportError(f"management web request {path} returned HTTP {response.status}")
        return parse_envelope(response.body)
