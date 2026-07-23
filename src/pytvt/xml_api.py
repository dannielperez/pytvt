#!/usr/bin/env python3
"""TVT NVR Web API client (NVMS-9000).

Pure-Python client for TVT NVR web CGI interface.  No external dependencies
beyond the standard library (+ ffmpeg binary for RTSP snapshots).

Capabilities
------------
- **Authentication** — challenge-response login via reqLogin / doLogin
- **Service control** — query / enable / disable RTSP & API Server
- **Network info** — port configuration, RTSP URLs
- **Camera inventory** — list channels, online status, per-camera IPs
- **Snapshots** — RTSP → JPEG capture via ffmpeg (IPC direct bypass)
- **User management** — list users, password-security policy
- **Password ops** — change admin password, bulk-update stored IPC creds

Authentication flow
-------------------
1. ``POST /reqLogin`` → receive *nonce*, *token*, session cookie
2. Hash: ``SHA512( MD5(password).hexdigest().upper() + "#" + nonce )``
3. ``POST /doLogin`` with hashed password → authenticated session

Password encoding (user management)
------------------------------------
``base64( MD5(password).hexdigest().upper() )``  — used by
``editUserPassword`` and the NVR's JavaScript front-end (SparkMD5).

Usage
-----
::

    from tvt_nvr_api import NvrClient

    with NvrClient("192.168.1.100", "admin", "YourPassword") as nvr:
        nvr.login()

        # Service status
        print(nvr.query_rtsp_server())
        nvr.ensure_services_enabled()

        # Camera inventory & snapshots
        for ch in nvr.query_channels():
            print(ch.chl_num, ch.name, ch.ip, ch.online)
        nvr.snapshot(1, "ch1.jpg")

        # User / password management
        print(nvr.query_users())
        print(nvr.query_password_security())
        nvr.change_own_password("OldPass", "NewPass")

        # Bulk credential sync (NVR stored creds, NOT IPC passwords)
        nvr.update_device_credentials(password="NewPass")

Scope & limitations
-------------------
- **NVR only** — this client targets the NVMS-9000 web CGI on TVT NVRs.
  IPC cameras have a completely different web interface (webpagev5 /
  NetAIIPCamera with LAPI REST + OCX plugin auth) and are NOT compatible.
- **IPC password change** — ``update_device_credentials()`` only updates
    the credentials the NVR *stores* for connecting to IPCs.  To change the
    actual password on an IPC camera you need the TVT SDK (port 9008) via
    a compatible SDK bridge.
- **addUser / editUser** — the NVR web CGI does not support creating or
  editing users (returns empty or permission errors).  Only
  ``editUserPassword`` (change own password) works.
- **RTSP snapshots** bypass the NVR's RTSP relay (which returns 401) and
  connect directly to each IPC camera via ``rtsp://user:pass@IPC_IP:554/profile1``.

Common workflows
----------------
**Enable services on an NVR** (required before RTSP snapshots)::

    with NvrClient("192.168.1.100", "admin", "Pass@") as nvr:
        nvr.login()
        nvr.ensure_services_enabled()   # enables RTSP + API Server if off
        print(nvr.query_rtsp_server())  # verify

**Change NVR admin password and sync stored IPC credentials**::

    with NvrClient("192.168.1.100", "admin", "OldPass") as nvr:
        nvr.login()
        result = nvr.change_admin_password_and_sync("OldPass", "NewPass")
        # NVR admin password changed, re-logged in, stored IPC creds updated

CLI
---
::

    # Service management
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' status
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' enable-rtsp
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' enable-api
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' enable-all
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' ports

    # Camera inventory & snapshots
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' channels
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' snapshot 1
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' snapshot-all

    # User & password management
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' users
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' password-security
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' change-password NewPass
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' sync-credentials
    python tvt_nvr_api.py 192.168.1.100 -p 'YourPassword' change-admin-and-sync NewPass
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import re
import subprocess
import sys
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from ._crypto import aes_ecb_zeropad
from .models import (
    AiResource,
    AiResourceChannel,
    AlarmServerConfig,
    ApiServerConfig,
    Channel,
    FaceDbGroup,
    FaceEvent,
    FacePerson,
    NvrApiError,
    NvrApiResponseShapeError,
    NvrFaceDetectionConfig,
    NvrLanFreeDevice,
    PasswordSecurity,
    PlatformAccessConfig,
    PlatformAccessDisabledError,
    PortConfig,
    RtspServerConfig,
    User,
)

XML_HEADER = '<?xml version="1.0" encoding="utf-8" ?>'
SYSTEM_TYPE = "NVMS-9000"

#: ``editIPChlPassword`` errorCode returned when the new password already equals
#: the camera's current password — an idempotent no-op, not a failure. Lets a
#: force-rotate treat an already-compliant camera as "already on target" instead
#: of counting it as an error.
IPC_PASSWORD_ALREADY_SET = "536870962"


class NvrClient:
    """Client for TVT NVR web CGI API (NVMS-9000).

    Provides a pure-Python interface to the NVR's XML-based web CGI.
    Use as a context manager for clean resource handling.

    Attributes:
        host: NVR IP address or hostname.
        username: Login username (typically ``admin``).
        password: Login password.
        port: HTTP port of the NVR web interface (default 80).
        timeout: HTTP request timeout in seconds.
    """

    def __init__(self, host: str, username: str, password: str, port: int = 80, timeout: int = 10):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self._cookie: str | None = None
        self._token: str | None = None
        self._logged_in = False
        self._session_key: str | None = None
        self._security_ver: str | None = None
        self._legacy_auth: str | None = None  # Basic auth header for legacy firmware
        self._login_nonce: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def _post(self, path: str, body: str) -> str:
        """POST XML to NVR and return response body."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
            if self._cookie:
                headers["Cookie"] = self._cookie
            if self._legacy_auth:
                headers["Authorization"] = self._legacy_auth
            conn.request("POST", f"/{path}", body=body.encode("utf-8"), headers=headers)
            resp = conn.getresponse()
            data = resp.read().decode("utf-8")

            # Capture session cookie from first request
            set_cookie = resp.getheader("Set-Cookie")
            if set_cookie:
                self._cookie = set_cookie.split(";")[0]

            return data
        finally:
            conn.close()

    def _build_request(self, cmd: str | None = None) -> str:
        """Build empty XML request with token."""
        token = self._token or "null"
        return (
            f'{XML_HEADER}<request version="1.0"  systemType="{SYSTEM_TYPE}" clientType="WEB">'
            f"<token>{token}</token></request>"
        )

    def _build_request_with_content(self, content: str) -> str:
        """Build XML request with content body and token."""
        token = self._token or "null"
        return (
            f'{XML_HEADER}<request version="1.0"   systemType="{SYSTEM_TYPE}" clientType="WEB">'
            f"<token>{token}</token>{content}</request>"
        )

    @staticmethod
    def _parse_xml_field(xml: str, field: str) -> str | None:
        """Extract a single XML field value (handles tags with attributes)."""
        m = re.search(rf"<{field}(?:\s[^>]*)?>(.+?)</{field}>", xml, re.DOTALL)
        return m.group(1).strip() if m else None

    @staticmethod
    def _check_response(xml: str, context: str = "") -> None:
        """Raise NvrApiError if response status is not success."""
        status = re.search(r"<status>(.*?)</status>", xml)
        if not status or status.group(1) != "success":
            error_code = re.search(r"<errorCode>(.*?)</errorCode>", xml)
            code = error_code.group(1) if error_code else None
            msg = "NVR API error"
            if context:
                msg += f" ({context})"
            if code:
                msg += f": errorCode={code}"
            raise NvrApiError(msg, code)

    @classmethod
    def _decrypt_session_key(cls, encrypted_key_b64: str, md5_password: str) -> str:
        raw = base64.b64decode(encrypted_key_b64)
        try:
            decrypted = aes_ecb_zeropad(raw, md5_password, decrypt=True)
        except RuntimeError as exc:
            raise NvrApiError(f"NVR session-key decrypt failed: {exc}") from exc
        return decrypted.rstrip(b"\x00").decode("utf-8")

    @classmethod
    def _encrypt_for_session(cls, plaintext: str, session_key: str) -> str:
        try:
            encrypted = aes_ecb_zeropad(plaintext.encode("utf-8"), session_key, decrypt=False)
        except RuntimeError as exc:
            raise NvrApiError(f"NVR request encryption failed: {exc}") from exc
        return base64.b64encode(encrypted).decode("ascii")

    def login(self) -> None:
        """Authenticate to the NVR web interface.

        Supports three firmware authentication variants:

        1. **Modern** (1.4.12+): ``reqLogin`` returns ``nonce`` + ``sessionId``;
           ``doLogin`` returns the ``token`` and optionally an encrypted
           ``sessionKey``.  Password is ``SHA512(MD5(pw).upper() + '#' + nonce)``.

        2. **Classic**: ``reqLogin`` returns ``nonce`` + ``token``.
           Same SHA512 hash, but the token comes from ``reqLogin``.

        3. **Legacy** (older firmware without nonce support): ``reqLogin``
           returns ``errorCode=536870934``.  Falls back to a direct
           ``doLogin`` with ``base64(password)`` and subsequent requests
           use ``Authorization: Basic base64(user:password)``.
        """
        # Step 1: Request login challenge
        req_body = self._build_request()
        data = self._post("reqLogin", req_body)

        nonce = self._parse_xml_field(data, "nonce")
        token = self._parse_xml_field(data, "token")

        if not nonce:
            # Check for specific error codes
            error_code = self._parse_xml_field(data, "errorCode")
            # 536870934 = legacy firmware that doesn't support nonce auth
            if error_code == "536870934":
                self._login_legacy()
                return
            raise NvrApiError(
                f"reqLogin failed: no nonce in response (errorCode={error_code})",
                error_code,
            )

        # Some firmware versions don't return a token in reqLogin;
        # doLogin will return it instead. Use "null" as placeholder.
        self._token = token  # may be None
        self._login_nonce = nonce

        # Step 2: Hash password = SHA512(MD5(password).upper() + "#" + nonce)
        md5_hex = hashlib.md5(self.password.encode()).hexdigest().upper()
        sha512_hex = hashlib.sha512((md5_hex + "#" + nonce).encode()).hexdigest()

        # Step 3: doLogin
        login_body = self._build_request_with_content(
            f"<content><userName><![CDATA[{self.username}]]></userName>"
            f"<password><![CDATA[{sha512_hex}]]></password></content>"
        )
        data = self._post("doLogin", login_body)
        self._check_response(data, "doLogin")

        # Pick up token from doLogin if reqLogin didn't provide one
        if not self._token:
            self._token = self._parse_xml_field(data, "token")

        encrypted_session_key = self._parse_xml_field(data, "sessionKey") or ""
        security_ver = self._parse_xml_field(data, "securityVer") or ""
        if encrypted_session_key:
            self._session_key = self._decrypt_session_key(encrypted_session_key, md5_hex)
        self._security_ver = security_ver or None

        self._logged_in = True

    def _login_legacy(self) -> None:
        """Legacy login for older firmware without nonce/challenge support.

        These devices ignore ``reqLogin`` (returning errorCode=536870934)
        and accept a direct ``doLogin`` with a base64-encoded password.
        Subsequent requests must include an ``Authorization: Basic`` header
        with ``base64(username:password)``.
        """
        b64_password = base64.b64encode(self.password.encode("utf-8")).decode("ascii")
        login_body = self._build_request_with_content(
            f"<content><userName><![CDATA[{self.username}]]></userName>"
            f"<password><![CDATA[{b64_password}]]></password></content>"
        )
        data = self._post("doLogin", login_body)
        self._check_response(data, "doLogin")

        # Legacy firmware uses HTTP Basic auth for subsequent requests
        creds = base64.b64encode(f"{self.username}:{self.password}".encode()).decode("ascii")
        self._legacy_auth = f"Basic {creds}"

        self._logged_in = True

    def _require_login(self) -> None:
        if not self._logged_in:
            raise NvrApiError("Not logged in. Call login() first.")

    # --- Query methods ---

    def query_rtsp_server(self) -> RtspServerConfig:
        """Query current RTSP server configuration."""
        self._require_login()
        data = self._post("queryRTSPServer", self._build_request())
        self._check_response(data, "queryRTSPServer")
        return RtspServerConfig(
            enabled=self._parse_xml_field(data, "rtspServerSwitch") == "true",
            port=int(self._parse_xml_field(data, "rtspPort") or 554),
            auth_type=self._parse_xml_field(data, "rtspAuthType") or "Digest",
            anonymous_access=self._parse_xml_field(data, "anonymousAccess") == "true",
        )

    def query_api_server(self) -> ApiServerConfig:
        """Query current API server configuration."""
        self._require_login()
        data = self._post("queryApiServer", self._build_request())
        self._check_response(data, "queryApiServer")
        # Extract content section to avoid matching <types> section
        content = self._parse_xml_field(data, "content") or data
        return ApiServerConfig(
            enabled=self._parse_xml_field(content, "apiserverSwitch") == "true",
            auth_type=self._parse_xml_field(content, "authenticationType") or "Digest",
        )

    def query_port_config(self) -> PortConfig:
        """Query network port configuration."""
        self._require_login()
        data = self._post("queryNetPortCfg", self._build_request())
        self._check_response(data, "queryNetPortCfg")
        return PortConfig(
            http_port=int(self._parse_xml_field(data, "httpPort") or 80),
            https_port=int(self._parse_xml_field(data, "httpsPort") or 443),
            server_port=int(self._parse_xml_field(data, "netPort") or 6036),
            rtsp_port=int(self._parse_xml_field(data, "rtspPort") or 554),
            pos_port=int(self._parse_xml_field(data, "posPort") or 9036),
            auto_report_port=int(self._parse_xml_field(data, "autoReportPort") or 2009),
        )

    def query_platform_access(self) -> PlatformAccessConfig:
        """Query Platform Access (Auto Report) configuration.

        Corresponds to: Function Panel → Integration → Platform Access
        in the NVR web UI.  This controls whether the NVR registers
        itself with a central management server (NVMS5000 / CMS).

        CGI endpoint: ``queryPlatformCfg``
        """
        self._require_login()
        data = self._post("queryPlatformCfg", self._build_request())
        self._check_response(data, "queryPlatformCfg")
        # Extract the NVMS5000 item from the list content
        item = re.search(r'<item\s+id="NVMS5000">(.*?)</item>', data, re.DOTALL)
        block = item.group(1) if item else data
        return PlatformAccessConfig(
            enabled=self._parse_xml_field(block, "switch") == "true",
            server_address=self._parse_xml_field(block, "serverAddr") or "",
            port=int(self._parse_xml_field(block, "port") or 2009),
            report_id=self._parse_xml_field(block, "reportId") or "",
        )

    def set_platform_access(
        self,
        *,
        enabled: bool,
        server_address: str,
        port: int = 2009,
        report_id: str,
    ) -> None:
        """Configure Platform Access (Auto Report) on the NVR.

        Args:
            enabled: Enable or disable the platform registration.
            server_address: CMS / NVMS5000 server hostname or IP.
            port: Platform protocol port (default 2009).
            report_id: Device report ID for the management server.

        CGI endpoint: ``editPlatformCfg``
        """
        self._require_login()
        content = (
            '<content type="list" current="NVMS5000">'
            '<item id="NVMS5000">'
            f"<switch>{str(enabled).lower()}</switch>"
            f"<serverAddr>{server_address}</serverAddr>"
            f"<port>{port}</port>"
            f"<reportId>{report_id}</reportId>"
            "</item>"
            "</content>"
        )
        data = self._post("editPlatformCfg", self._build_request_with_content(content))
        # errorCode 536870943 means Platform Access was never enabled on this NVR
        error_code = re.search(r"<errorCode>(.*?)</errorCode>", data)
        if error_code and error_code.group(1) == "536870943":
            raise PlatformAccessDisabledError(
                f"Platform Access is disabled on {self.host} — enable it in the "
                "NVR web UI (Integration → Platform Access) before editing config",
                error_code=error_code.group(1),
            )
        self._check_response(data, "editPlatformCfg")

    def query_channels(self) -> list[Channel]:
        """Query the list of cameras/channels connected to the NVR."""
        self._require_login()
        dev_data = self._post("queryDevList", self._build_request())
        self._check_response(dev_data, "queryDevList")

        channels: list[Channel] = []
        n_parsed = len(re.findall(r'<item\s+id="([^"]+)">', dev_data))
        # False-empty robustness: a success status with an
        # unrecognized payload shape must NOT silently look like a genuine-empty
        # channel list. A recognized queryDevList response always wraps results
        # in a <content ...> container; when present it carries total="N". Signal
        # shape drift (no container, or total>0 yet 0 items parsed) so the caller
        # can tell "fetch failed / shape changed" from "really has no channels".
        content = re.search(r"<content\b[^>]*>", dev_data)
        if content is None:
            raise NvrApiResponseShapeError(
                "queryDevList returned success but no <content> container — "
                "unrecognized response shape (possible firmware drift)"
            )
        total_m = re.search(r'\btotal="(\d+)"', content.group(0))
        if total_m is not None:
            total = int(total_m.group(1))
            if total > 0 and n_parsed == 0:
                raise NvrApiResponseShapeError(
                    f"queryDevList declared total={total} but parsed 0 channel "
                    "items — unrecognized item shape (possible firmware drift)"
                )

        # Get online channel IDs
        online_data = self._post("queryOnlineChlList", self._build_request())
        online_ids: set[str] = set()
        if "<status>success</status>" in online_data:
            online_ids = set(re.findall(r'<item\s+id="([^"]+)"', online_data))

        for m in re.finditer(r'<item\s+id="([^"]+)">(.*?)</item>', dev_data, re.DOTALL):
            dev_id = m.group(1)
            block = m.group(2)
            chl_num = int(self._parse_xml_field(block, "chlNum") or 0)
            channels.append(
                Channel(
                    chl_num=chl_num,
                    name=self._parse_xml_field(block, "name") or "",
                    ip=self._parse_xml_field(block, "ip") or "",
                    port=int(self._parse_xml_field(block, "port") or 9008),
                    dev_id=dev_id,
                    model=self._parse_xml_field(block, "productModel") or "",
                    manufacturer=self._parse_xml_field(block, "manufacturer") or "",
                    protocol=self._parse_xml_field(block, "protocolType") or "",
                    online=dev_id in online_ids,
                    add_type=self._parse_xml_field(block, "addType") or "",
                    poe_index=int(self._parse_xml_field(block, "poeIndex") or 0),
                    chl_type=self._parse_xml_field(block, "chlType") or "",
                    access_type=self._parse_xml_field(block, "AccessType") or "",
                    auto_report_id=self._parse_xml_field(block, "autoReportID") or "",
                    guid=self.channel_guid(chl_num),
                )
            )
        return channels

    def query_nvr_lan_free_devices(self) -> list[NvrLanFreeDevice]:
        """Query devices this NVR can see on its local LAN but has not added.

        This is useful when the platform can reach an NVR over routed VPN/NAT,
        but the cameras themselves are only visible from the recorder's local
        subnet.
        """
        self._require_login()
        data = self._post("queryLanFreeDeviceList", self._build_request())
        self._check_response(data, "queryLanFreeDeviceList")

        devices: list[NvrLanFreeDevice] = []
        for block in re.findall(r"<item>(.*?)</item>", data, re.DOTALL):
            activate_status = self._parse_xml_field(block, "activateStatus") or ""
            devices.append(
                NvrLanFreeDevice(
                    ip=self._parse_xml_field(block, "ip") or "",
                    mask=self._parse_xml_field(block, "mask") or "",
                    gateway=self._parse_xml_field(block, "gateway") or "",
                    mac=(self._parse_xml_field(block, "mac") or "").upper(),
                    port=int(self._parse_xml_field(block, "port") or 9008),
                    http_port=int(self._parse_xml_field(block, "httpPort") or 80),
                    protocol=self._parse_xml_field(block, "protocolType") or "",
                    manufacturer=self._parse_xml_field(block, "manufacturer") or "",
                    model=self._parse_xml_field(block, "productModel") or "",
                    name=self._parse_xml_field(block, "name") or "",
                    serial_number=self._parse_xml_field(block, "serialNum") or "",
                    local_eth_name=self._parse_xml_field(block, "localEthName") or "",
                    sub_ip=self._parse_xml_field(block, "subIp") or "",
                    sub_ip_netmask=self._parse_xml_field(block, "subIpNetMask") or "",
                    activated=(
                        True if activate_status == "ACTIVATED" else False if activate_status == "UNACTIVATED" else None
                    ),
                    activate_status=activate_status,
                    industry_product_type=self._parse_xml_field(block, "industryProductType") or "",
                    device_type=self._parse_xml_field(block, "devType") or "",
                )
            )
        return devices

    def query_lan_free_devices(self) -> list[NvrLanFreeDevice]:
        """Backward-compatible alias for :meth:`query_nvr_lan_free_devices`."""
        return self.query_nvr_lan_free_devices()

    def edit_nvr_lan_device_network(
        self,
        *,
        old_ip: str,
        new_ip: str,
        netmask: str,
        gateway: str,
        username: str,
        password: str,
    ) -> None:
        """Edit a free LAN device's network settings through the NVR UI API."""
        self._require_login()
        if not self._session_key:
            raise NvrApiError("Missing NVR session key; call login() first.")

        attrs = f' securityVer="{self._security_ver}"' if self._security_ver else ""
        encrypted_password = self._encrypt_for_session(password, self._session_key)
        body = self._build_request_with_content(
            "<content><device><item id='1'>"
            f"<oldIP>{old_ip}</oldIP>"
            f"<newIP>{new_ip}</newIP>"
            f"<netmask>{netmask}</netmask>"
            f"<gateway>{gateway}</gateway>"
            f"<username>{username}</username>"
            f"<password{attrs}><![CDATA[{encrypted_password}]]></password>"
            "</item></device></content>"
        )
        data = self._post("editDevNetworkList", body)
        self._check_response(data, "editDevNetworkList")

        item_error = self._parse_xml_field(data, "errorCode")
        if item_error and item_error != "0":
            raise NvrApiError("NVR LAN device network edit failed", item_error)

    def delete_nvr_devices(
        self,
        dev_ids: list[str],
        *,
        poe_indexes_by_id: dict[str, int | str] | None = None,
    ) -> int:
        """Delete configured devices/channels from the NVR via ``delDevList``."""
        self._require_login()
        if not dev_ids:
            return 0

        items = []
        for dev_id in dev_ids:
            item = f'<item id="{dev_id}">'
            if poe_indexes_by_id and dev_id in poe_indexes_by_id:
                poe_index = str(poe_indexes_by_id[dev_id]).strip()
                if poe_index and poe_index != "0":
                    item += f"<poeIndex>{poe_index}</poeIndex>"
            item += "</item>"
            items.append(item)

        body = self._build_request_with_content(f'<condition><devIds type="list">{"".join(items)}</devIds></condition>')
        data = self._post("delDevList", body)
        self._check_response(data, "delDevList")
        return len(dev_ids)

    # XML envelope blocks captured from the NVMS-9000 web UI "Quickly Add" flow.
    _ADD_TYPES = (
        "<types><manufacturer>"
        '<enum displayName="IP CAM">TVT</enum>'
        '<enum displayName="Onvif CAM">ONVIF</enum>'
        '<enum displayName="HIKVISION">HIKVISION</enum>'
        '<enum displayName="DAHUA">DAHUA</enum>'
        "</manufacturer><protocolType>"
        "<enum>TVT_IPCAMERA</enum><enum>ONVIF</enum>"
        "</protocolType></types>"
    )
    _ADD_ITEMTYPE = '<itemType><manufacturer type="manufacturer"/><protocolType type="protocolType"/></itemType>'

    def add_nvr_devices(self, devices: list[dict]) -> str:
        """Add cameras as NVR channels via the ``createDevList`` CGI.

        Request shapes captured from the NVMS-9000 web UI (NOT
        ``addDevList``/``editDevList`` — those don't add channels on this
        firmware). Two per-item modes, chosen automatically:

        * **With an explicit ``password``** (manual-add): the password is
          session-encrypted (``<password securityVer=...>`` CDATA, like
          :meth:`edit_nvr_lan_device_network`). Use this for cameras whose
          password differs from the NVR's default. ``mac`` not required.
        * **Without a password** (quick-add of a discovered free device): the NVR
          authenticates with its stored *default camera password* (the "Camera
          Default Password" / ``queryDevDefaultPwd`` setting), so the channel only
          comes ONLINE if the camera's password matches that default. Requires
          ``mac``.

        Device dict keys: ``ip`` (required); ``password`` (optional — selects the
        mode); ``mac`` (required for the no-password mode); plus optional ``name``
        (default = ip), ``port`` (9008), ``username`` ("admin"), ``manufacturer``
        ("TVT"), ``protocol`` ("TVT_IPCAMERA"), ``model``, ``factory_name``
        ("EAST"), ``access_type`` ("NORMAL"), ``local_eth`` ("eth0"), ``index`` (0).

        Returns the raw response XML. Raises NvrApiError on a top-level failure
        (e.g. errorCode 536871004 = NVR channel maximum reached).
        """
        self._require_login()
        if not devices:
            return ""
        sec_attr = f' securityVer="{self._security_ver}"' if self._security_ver else ""
        items = []
        for d in devices:
            name = d.get("name", d["ip"])
            common = (
                f"<name><![CDATA[{name}]]></name>"
                f"<ip>{d['ip']}</ip>"
                f"<port>{d.get('port', 9008)}</port>"
                f"<userName><![CDATA[{d.get('username', 'admin')}]]></userName>"
            )
            pw = d.get("password")
            if pw is not None:
                if not self._session_key:
                    raise NvrApiError("createDevList with password needs a session key; call login() first.")
                enc = self._encrypt_for_session(pw, self._session_key)
                common += f"<password{sec_attr}><![CDATA[{enc}]]></password>"
            tail = (
                f"<index>{d.get('index', 0)}</index>"
                f"<manufacturer>{d.get('manufacturer', 'TVT')}</manufacturer>"
                f"<protocolType>{d.get('protocol', 'TVT_IPCAMERA')}</protocolType>"
                f'<productModel factoryName="{d.get("factory_name", "EAST")}">{d.get("model", "")}</productModel>'
                f"<accessType>{d.get('access_type', 'NORMAL')}</accessType>"
                '<rec per="5" post="10"/>'
                "<snapSwitch>true</snapSwitch>"
                "<buzzerSwitch>false</buzzerSwitch>"
                "<popVideoSwitch>false</popVideoSwitch>"
                "<frontEndOffline_popMsgSwitch>true</frontEndOffline_popMsgSwitch>"
            )
            if pw is None:
                # quick-add of a discovered free device — carries L2 identity
                tail += (
                    f"<localEthName>{d.get('local_eth', 'eth0')}</localEthName>"
                    f"<mac>{d['mac']}</mac>"
                    "<allowAssignIP>true</allowAssignIP>"
                )
            items.append("<item>" + common + tail + "</item>")
        content = self._ADD_TYPES + '<content type="list">' + self._ADD_ITEMTYPE + "".join(items) + "</content>"
        data = self._post("createDevList", self._build_request_with_content(content))
        self._check_response(data, "createDevList")
        return data

    def edit_nvr_ipc_passwords(
        self,
        channel_ids: list[str],
        *,
        new_password: str,
    ) -> int:
        """Change actual IPC passwords for configured TVT channels via the NVR."""
        self._require_login()
        if not channel_ids:
            return 0
        if not self._session_key:
            raise NvrApiError("Missing NVR session key; call login() first.")

        encrypted_password = self._encrypt_for_session(new_password, self._session_key)
        attrs = f' securityVer="{self._security_ver}"' if self._security_ver else ""

        updated = 0
        failures: list[str] = []
        for channel_id in channel_ids:
            body = self._build_request_with_content(
                f"<content><chl id='{channel_id}'><password{attrs}><![CDATA[{encrypted_password}]]>"
                f"</password></chl></content>"
            )
            data = self._post("editIPChlPassword", body)
            if "<status>success</status>" in data:
                updated += 1
            else:
                failures.append(channel_id)

        if failures:
            raise NvrApiError(f"Failed to change IPC password for {len(failures)} channel(s): {', '.join(failures)}")
        return updated

    def edit_ipc_password_status(self, channel_id: str, *, new_password: str) -> str:
        """Change one channel's IPC password and report whether it changed.

        Returns ``"changed"`` when the camera password was updated, or
        ``"already-set"`` when the NVR reports the new password already equals the
        camera's current one (errorCode :data:`IPC_PASSWORD_ALREADY_SET`) — an
        idempotent no-op. Any other non-success response raises
        :class:`NvrApiError` carrying the errorCode.

        Unlike :meth:`edit_nvr_ipc_passwords` (which treats every non-success as a
        failure), distinguishing "already on target" lets a caller force-rotate a
        whole site — including channels the NVR currently shows online — without
        knowing the old password and without a benign no-op reading as an error.
        """
        self._require_login()
        if not self._session_key:
            raise NvrApiError("Missing NVR session key; call login() first.")

        encrypted_password = self._encrypt_for_session(new_password, self._session_key)
        attrs = f' securityVer="{self._security_ver}"' if self._security_ver else ""
        body = self._build_request_with_content(
            f"<content><chl id='{channel_id}'><password{attrs}><![CDATA[{encrypted_password}]]>"
            f"</password></chl></content>"
        )
        data = self._post("editIPChlPassword", body)
        if "<status>success</status>" in data:
            return "changed"
        code = self._parse_xml_field(data, "errorCode")
        if code == IPC_PASSWORD_ALREADY_SET:
            return "already-set"
        raise NvrApiError(f"editIPChlPassword failed for {channel_id}", code)

    def get_rtsp_url(self, channel: int, stream_type: str = "main") -> str:
        """Build RTSP URL for an NVR channel (1-indexed).

        Uses the IPC camera directly via its IP instead of the NVR's RTSP relay,
        since NVR RTSP auth is unreliable. Falls back to NVR relay if the
        channel's IP is not available.

        Args:
            channel: Channel number (1-indexed as returned by query_channels)
            stream_type: 'main', 'sub', or 'third'
        """
        from urllib.parse import quote

        user = quote(self.username, safe="")
        pwd = quote(self.password, safe="")
        profile = {"main": "profile1", "sub": "profile2", "third": "profile3"}.get(stream_type, "profile1")

        # Try to find the IPC IP for this channel
        channels = self.query_channels()
        for ch in channels:
            if ch.chl_num == channel and ch.ip:
                return f"rtsp://{user}:{pwd}@{ch.ip}:554/{profile}"

        # Fallback to NVR relay
        rtsp = self.query_rtsp_server()
        return f"rtsp://{user}:{pwd}@{self.host}:{rtsp.port}/chID={channel}&streamType={stream_type}"

    # --- Edit methods ---

    def enable_rtsp(self, port: int = 554, auth_type: str = "Digest", anonymous: bool = False) -> None:
        """Enable RTSP server on the NVR (editRTSPServer).

        Args:
            port: RTSP port (default 554).
            auth_type: Authentication type ('Digest' or 'Basic').
            anonymous: Allow anonymous (unauthenticated) RTSP access.
        """
        self._require_login()
        content = (
            f"<content>"
            f"<rtspServerSwitch>true</rtspServerSwitch>"
            f"<rtspAuthType>{auth_type}</rtspAuthType>"
            f"<rtspPort>{port}</rtspPort>"
            f"<anonymousAccess>{str(anonymous).lower()}</anonymousAccess>"
            f"</content>"
        )
        data = self._post("editRTSPServer", self._build_request_with_content(content))
        self._check_response(data, "editRTSPServer")

    def disable_rtsp(self) -> None:
        """Disable RTSP server on the NVR."""
        self._require_login()
        rtsp = self.query_rtsp_server()
        content = (
            f"<content>"
            f"<rtspServerSwitch>false</rtspServerSwitch>"
            f"<rtspAuthType>{rtsp.auth_type}</rtspAuthType>"
            f"<rtspPort>{rtsp.port}</rtspPort>"
            f"<anonymousAccess>{str(rtsp.anonymous_access).lower()}</anonymousAccess>"
            f"</content>"
        )
        data = self._post("editRTSPServer", self._build_request_with_content(content))
        self._check_response(data, "editRTSPServer")

    def enable_api_server(self, auth_type: str = "Digest") -> None:
        """Enable API server on the NVR (editApiServer).

        Args:
            auth_type: Authentication type ('Digest' or 'Basic').
        """
        self._require_login()
        content = (
            f"<content>"
            f"<apiserverSwitch>true</apiserverSwitch>"
            f"<authenticationType>{auth_type}</authenticationType>"
            f"</content>"
        )
        data = self._post("editApiServer", self._build_request_with_content(content))
        self._check_response(data, "editApiServer")

    def disable_api_server(self) -> None:
        """Disable API server on the NVR."""
        self._require_login()
        api = self.query_api_server()
        content = (
            f"<content>"
            f"<apiserverSwitch>false</apiserverSwitch>"
            f"<authenticationType>{api.auth_type}</authenticationType>"
            f"</content>"
        )
        data = self._post("editApiServer", self._build_request_with_content(content))
        self._check_response(data, "editApiServer")

    def ensure_services_enabled(self) -> dict[str, bool]:
        """Ensure both RTSP and API Server are enabled.

        Checks current state and enables only what's disabled.

        Returns:
            Dict of services that were changed, e.g. ``{'rtsp': True, 'api_server': True}``.
            Empty dict if both were already enabled.
        """
        self._require_login()
        changed = {}

        rtsp = self.query_rtsp_server()
        if not rtsp.enabled:
            self.enable_rtsp(port=rtsp.port, auth_type=rtsp.auth_type)
            changed["rtsp"] = True

        api = self.query_api_server()
        if not api.enabled:
            self.enable_api_server(auth_type=api.auth_type)
            changed["api_server"] = True

        return changed

    # --- User management ---

    def query_users(self) -> list[User]:
        """List all users on the NVR (queryUserList).

        Returns:
            List of User dataclasses with id, username, type, enabled status,
            auth group, email, and MAC binding info.
        """
        self._require_login()
        data = self._post("queryUserList", self._build_request())
        self._check_response(data, "queryUserList")
        users: list[User] = []
        for m in re.finditer(r'<item id="([^"]+)">(.*?)</item>', data, re.DOTALL):
            uid = m.group(1)
            block = m.group(2)
            name_m = re.search(r"<userName><!\[CDATA\[(.*?)\]\]></userName>", block)
            users.append(
                User(
                    user_id=uid,
                    username=name_m.group(1) if name_m else "",
                    user_type=self._parse_xml_field(block, "userType") or "normal",
                    enabled=self._parse_xml_field(block, "enabled") == "true",
                    auth_group=self._parse_xml_field(block, "authGroup") or "",
                    email=self._parse_xml_field(block, "email") or "",
                    bind_mac=self._parse_xml_field(block, "bindMacSwitch") == "true",
                    mac=self._parse_xml_field(block, "mac") or "00:00:00:00:00:00",
                )
            )
        return users

    def query_password_security(self) -> PasswordSecurity:
        """Query password complexity requirements (queryPasswordSecurity).

        Returns:
            PasswordSecurity with minimum strength level (weak/medium/strong/stronger),
            expiration policy in days (0 = never), and list of allowed strength levels.
        """
        self._require_login()
        data = self._post("queryPasswordSecurity", self._build_request())
        self._check_response(data, "queryPasswordSecurity")
        levels = re.findall(r"<enum>(weak|medium|strong|stronger)</enum>", data)
        content = self._parse_xml_field(data, "content") or data
        strength = self._parse_xml_field(content, "userPasswordAllowLevel") or "weak"
        expiry = self._parse_xml_field(content, "userPasswordExpirationTime") or "0"
        return PasswordSecurity(
            min_strength=strength,
            expiration_days=int(expiry),
            allowed_levels=levels,
        )

    @staticmethod
    def _encode_password(password: str) -> str:
        """Encode a password for NVR user management endpoints.

        Format: base64(MD5(password).hexdigest().upper())
        SparkMD5 returns uppercase hex; NVR expects base64 of that.
        """
        md5_hex = hashlib.md5(password.encode()).hexdigest().upper()
        return base64.b64encode(md5_hex.encode()).decode()

    def _ui_auth_hash(self) -> str:
        """Hash used by web UI auth dialogs for privileged actions.

        TVT firmware expects SHA512(MD5(password).upper() + "#" + nonce)
        for privileged <auth> actions (validated against live UserMgr flow).
        """
        nonce = self._login_nonce or ""
        md5_hex = hashlib.md5(self.password.encode()).hexdigest().upper()
        return hashlib.sha512((md5_hex + "#" + nonce).encode()).hexdigest()

    def _set_user_email_web_ui(self, user: User, target_email: str) -> dict:
        """Try editUser with web-UI payload shape (includes <auth> block).

        Some firmware rejects direct editUser/editUserList payloads unless the
        request matches the browser's UserMgr flow.
        """
        auth_group_id = (user.auth_group or "Administrator").strip() or "Administrator"
        auth_hash = self._ui_auth_hash()

        payload_variants = [
            (
                "webui_full",
                (
                    "<content>"
                    f"<userId>{escape(user.user_id)}</userId>"
                    f"<userName><![CDATA[{user.username}]]></userName>"
                    f'<authGroup id ="{escape(auth_group_id)}" ></authGroup>'
                    f"<bindMacSwitch>{'true' if user.bind_mac else 'false'}</bindMacSwitch>"
                    "<modifyPassword>false</modifyPassword>"
                    f"<mac><![CDATA[{escape(user.mac or '00:00:00:00:00:00')}]]></mac>"
                    f"<email><![CDATA[{target_email}]]></email>"
                    f"<enabled>{'true' if user.enabled else 'false'}</enabled>"
                    "</content>"
                    "<auth>"
                    f"<userName>{escape(self.username)}</userName>"
                    f"<password>{auth_hash}</password>"
                    "</auth>"
                ),
            ),
            (
                "webui_minimal",
                (
                    "<content>"
                    f"<userId>{escape(user.user_id)}</userId>"
                    f"<userName><![CDATA[{user.username}]]></userName>"
                    f"<email><![CDATA[{target_email}]]></email>"
                    "<modifyPassword>false</modifyPassword>"
                    "</content>"
                    "<auth>"
                    f"<userName>{escape(self.username)}</userName>"
                    f"<password>{auth_hash}</password>"
                    "</auth>"
                ),
            ),
        ]

        attempts: list[str] = []
        for variant, content in payload_variants:
            data = self._post("editUser", self._build_request_with_content(content))
            status = self._parse_xml_field(data, "status")
            if status == "success":
                return {"ok": True, "command": "editUser", "variant": variant}
            error_code = self._parse_xml_field(data, "errorCode") or "unknown"
            attempts.append(f"{variant}:status={status or 'missing'}:error={error_code}")

        raise NvrApiError("webui editUser failed; " + " | ".join(attempts))

    def change_own_password(self, old_password: str, new_password: str) -> None:
        """Change the current user's password (editUserPassword).

        WARNING: This invalidates the session. You must re-login after.
        """
        self._require_login()
        old_enc = self._encode_password(old_password)
        new_enc = self._encode_password(new_password)
        content = (
            f"<content>"
            f"<oldPassword><![CDATA[{old_enc}]]></oldPassword>"
            f"<password><![CDATA[{new_enc}]]></password>"
            f"</content>"
        )
        data = self._post("editUserPassword", self._build_request_with_content(content))
        self._check_response(data, "editUserPassword")
        # Session is invalidated after password change
        self._logged_in = False
        self._cookie = None
        self._token = None

    def set_user_email(self, *, username: str = "admin", email: str, verify: bool = True) -> dict:
        """Set/reset-email value for a user account via the NVR web CGI.

        The API shape varies across firmware, so this method tries supported
        command names in order and verifies by re-querying users.

        Args:
            username: Account to update (default ``admin``).
            email: Email address to set on the account.
            verify: Re-read user list and confirm persisted email.

        Returns:
            Dict with update metadata (command used, previous/new email).
        """
        self._require_login()
        target = username.strip()
        if not target:
            raise NvrApiError("username cannot be empty")
        target_email = email.strip()
        if not target_email:
            raise NvrApiError("email cannot be empty")

        users = self.query_users()
        user = next((u for u in users if u.username.lower() == target.lower()), None)
        if user is None:
            raise NvrApiError(f"User not found: {target}")

        payload = (
            '<content type="list">'
            f'<item id="{escape(user.user_id)}">'
            f"<userName><![CDATA[{user.username}]]></userName>"
            f"<userType>{escape(user.user_type or 'normal')}</userType>"
            f"<enabled>{'true' if user.enabled else 'false'}</enabled>"
            f"<authGroup>{escape(user.auth_group or 'Administrator')}</authGroup>"
            f"<email><![CDATA[{target_email}]]></email>"
            f"<bindMacSwitch>{'true' if user.bind_mac else 'false'}</bindMacSwitch>"
            f"<mac>{escape(user.mac or '00:00:00:00:00:00')}</mac>"
            "</item>"
            "</content>"
        )

        attempts: list[str] = []
        for cmd in ("editUser", "editUserList"):
            data = self._post(cmd, self._build_request_with_content(payload))
            status = self._parse_xml_field(data, "status")
            if status != "success":
                error_code = self._parse_xml_field(data, "errorCode") or "unknown"
                attempts.append(f"{cmd}:status={status or 'missing'}:error={error_code}")
                continue

            if verify:
                refreshed = self.query_users()
                updated = next((u for u in refreshed if u.username.lower() == target.lower()), None)
                if updated is None:
                    attempts.append(f"{cmd}:verify=user_missing")
                    continue
                if (updated.email or "").strip() != target_email:
                    got = (updated.email or "").strip()
                    attempts.append(f"{cmd}:verify=email_mismatch:{got!r}")
                    continue

            return {
                "ok": True,
                "command": cmd,
                "user_id": user.user_id,
                "username": user.username,
                "old_email": user.email,
                "new_email": target_email,
            }

        # Fallback: replay the browser's edit-user payload shape.
        try:
            webui = self._set_user_email_web_ui(user, target_email)
            if verify:
                try:
                    refreshed = self.query_users()
                except NvrApiError as exc:
                    # Some firmware invalidates/changes auth context after
                    # editUser on the current account; re-login then re-query.
                    if exc.error_code in {"536871011", "536870948"}:
                        self.login()
                        refreshed = self.query_users()
                    else:
                        raise
                updated = next((u for u in refreshed if u.username.lower() == target.lower()), None)
                if updated is None:
                    attempts.append("webui:verify=user_missing")
                elif (updated.email or "").strip() != target_email:
                    got = (updated.email or "").strip()
                    attempts.append(f"webui:verify=email_mismatch:{got!r}")
                else:
                    return {
                        "ok": True,
                        "command": webui.get("command", "editUser"),
                        "variant": webui.get("variant", "webui"),
                        "user_id": user.user_id,
                        "username": user.username,
                        "old_email": user.email,
                        "new_email": target_email,
                    }
            else:
                return {
                    "ok": True,
                    "command": webui.get("command", "editUser"),
                    "variant": webui.get("variant", "webui"),
                    "user_id": user.user_id,
                    "username": user.username,
                    "old_email": user.email,
                    "new_email": target_email,
                }
        except NvrApiError as exc:
            attempts.append(str(exc))

        raise NvrApiError("set_user_email failed; " + " | ".join(attempts))

    def set_admin_email(self, *, email: str, verify: bool = True) -> dict:
        """Set/reset-email value on the ``admin`` account.

        This is a convenience wrapper for callers that only target the
        admin user and prefer an explicit method name.
        """
        return self.set_user_email(username="admin", email=email, verify=verify)

    def query_secure_email(self) -> dict:
        """Query the Password Reset via E-mail configuration (querySecureEmailcfg).

        Returns:
            Dict with ``enabled`` (bool) and ``email`` (str) keys.
        """
        self._require_login()
        data = self._post("querySecureEmailcfg", self._build_request())
        self._check_response(data, "querySecureEmailcfg")
        raw_email = self._parse_xml_field(data, "email") or ""
        # Strip CDATA wrapper if present (some firmware returns literal CDATA text)
        if raw_email.startswith("<![CDATA[") and raw_email.endswith("]]>"):
            raw_email = raw_email[9:-3]
        return {
            "enabled": self._parse_xml_field(data, "switch") == "true",
            "email": raw_email,
        }

    def set_secure_email(self, *, email: str, enabled: bool = True, verify: bool = True) -> dict:
        """Set the Password Reset via E-mail address and enable/disable it.

        This is the global NVR-level password-reset email (Function Panel >
        Edit Security Question > Password Reset via E-mail), distinct from the
        per-user reset email set by ``set_user_email``.

        Args:
            email: Email address to set.
            enabled: Whether to enable the Password Reset feature (default True).
            verify: Re-query and confirm the value persisted.

        Returns:
            Dict with update metadata.
        """
        self._require_login()
        target_email = email.strip()
        if not target_email:
            raise NvrApiError("email cannot be empty")

        old = self.query_secure_email()
        auth_hash = self._ui_auth_hash()

        content = (
            "<content>"
            f"<switch>{'true' if enabled else 'false'}</switch>"
            f"<email><![CDATA[{target_email}]]></email>"
            "</content>"
            "<auth>"
            f"<userName>{escape(self.username)}</userName>"
            f"<password>{auth_hash}</password>"
            "</auth>"
        )
        data = self._post("editSecureEMailCfg", self._build_request_with_content(content))
        self._check_response(data, "editSecureEMailCfg")

        if verify:
            updated = self.query_secure_email()
            if updated["email"].strip() != target_email or updated["enabled"] != enabled:
                raise NvrApiError(
                    f"set_secure_email verify failed: got email={updated['email']!r} enabled={updated['enabled']}"
                )

        return {
            "ok": True,
            "command": "editSecureEMailCfg",
            "old_email": old["email"],
            "new_email": target_email,
            "enabled": enabled,
        }

    def update_device_credentials(
        self, dev_ids: list[str] | None = None, username: str = "admin", password: str | None = None
    ) -> int:
        """Update stored credentials for IPC cameras on the NVR (editDevList).

        This changes what the NVR uses to connect to IPC cameras.
        It does NOT change the actual password on the IPC cameras themselves.

        Args:
            dev_ids: List of device IDs to update (None = all devices)
            username: Username to store
            password: Password to store (None = use current NVR password)

        Returns:
            Number of devices updated
        """
        self._require_login()
        pwd = password or self.password

        if dev_ids is None:
            channels = self.query_channels()
            dev_ids = [ch.dev_id for ch in channels]

        items = []
        for did in dev_ids:
            items.append(
                f'<item id="{did}">'
                f"<userName><![CDATA[{username}]]></userName>"
                f"<password><![CDATA[{pwd}]]></password>"
                f"</item>"
            )

        content = '<content type="list">' + "".join(items) + "</content>"
        data = self._post("editDevList", self._build_request_with_content(content))
        self._check_response(data, "editDevList")
        return len(items)

    def edit_nvr_channel_credentials(
        self,
        dev_ids: list[str] | None = None,
        *,
        username: str = "admin",
        password: str | None = None,
    ) -> int:
        """Alias for ``editDevList`` with NVR-specific naming.

        This updates the credentials the NVR stores for already-added
        channels. It does not rotate the actual camera password.
        """
        return self.update_device_credentials(dev_ids=dev_ids, username=username, password=password)

    def change_admin_password_and_sync(self, old_password: str, new_password: str) -> dict:
        """Change NVR admin password and update all stored IPC credentials to match.

        Steps:
        1. Update stored IPC credentials to new_password via editDevList
        2. Change admin password via editUserPassword (invalidates session)
        3. Re-login with new password
        4. Verify login success

        Args:
            old_password: Current admin password
            new_password: New password to set

        Returns:
            dict with 'devices_updated' count and 'password_changed' bool

        NOTE: This does NOT change the actual IPC camera passwords.
        To change IPC camera passwords, use the SDK or change them
        individually, then call update_device_credentials().
        """
        self._require_login()
        result: dict = {"devices_updated": 0, "password_changed": False}

        # Step 1: Update stored IPC credentials first (while still logged in)
        result["devices_updated"] = self.update_device_credentials(password=new_password)

        # Step 2: Change admin password (invalidates session)
        self.change_own_password(old_password, new_password)
        result["password_changed"] = True

        # Step 3: Re-login with new password
        self.password = new_password
        self.login()

        return result

    def snapshot(self, channel: int, output_path: str, stream_type: str = "main", timeout: int = 10) -> bool:
        """Capture a JPEG snapshot from an NVR channel via RTSP+ffmpeg.

        Args:
            channel: Channel number (1-indexed)
            output_path: Output JPEG file path
            stream_type: 'main' or 'sub'
            timeout: ffmpeg timeout in seconds

        Returns:
            True if snapshot was saved successfully
        """
        url = self.get_rtsp_url(channel, stream_type)
        return rtsp_snapshot(url, output_path, timeout=timeout)

    # ── AI / Face Recognition ────────────────────────────────────────
    #
    # NVR-side ("back-end") face analytics: the Function Panel → AI Event →
    # Face Recognition page. Per-channel commands key off the channel GUID
    # ({0000000N-...}); use :meth:`channel_guid` (or ``Channel.guid``) to build
    # it from a 1-indexed channel number.

    @staticmethod
    def channel_guid(channel: int) -> str:
        """Return the NVR channel GUID for a 1-indexed channel number.

        The AI/face CGI commands identify a channel by a fixed-format GUID —
        e.g. channel 9 → ``{00000009-0000-0000-0000-000000000000}`` — rather
        than by the ``chlNum`` used elsewhere.
        """
        if channel < 1:
            raise ValueError("channel must be >= 1")
        return "{" + f"{channel:08d}" + "-0000-0000-0000-000000000000}"

    def query_ai_resource(self) -> AiResource:
        """Query the NVR's AI-compute pool and per-channel allocation.

        This is what lets an NVR run analytics on an ordinary camera: the
        recorder has a finite pool of AI resource and allocates slices to
        channels. Returns the supported event types (``faceMatch``,
        ``faceDetect``, ``tripwire``, ``perimeter``), the total occupancy, and
        each channel's current allocation.

        CGI endpoint: ``queryAIResourceDetail``
        """
        self._require_login()
        data = self._post("queryAIResourceDetail", self._build_request())
        self._check_response(data, "queryAIResourceDetail")

        supported = re.findall(r"<eventType>(.*?)</eventType>", data, re.DOTALL)
        supported_types: list[str] = []
        if supported:
            supported_types = re.findall(r"<enum>(.*?)</enum>", supported[0])
        total = float(self._parse_xml_field(data, "totalResourceOccupancy") or 0)

        content = re.search(r"<content\b[^>]*>(.*?)</content>", data, re.DOTALL)
        channels: list[AiResourceChannel] = []
        if content:
            for m in re.finditer(r"<item(\s+[^>]*)?>(.*?)</item>", content.group(1), re.DOTALL):
                attrs = m.group(1) or ""
                block = m.group(2)
                guid = re.search(r'(?:id|guid|chlId)="([^"]+)"', attrs)
                channels.append(
                    AiResourceChannel(
                        chl_id=(guid.group(1) if guid else self._parse_xml_field(block, "chlId") or ""),
                        name=self._parse_xml_field(block, "name") or "",
                        event_types=re.findall(r"<enum>(.*?)</enum>", block)
                        or ([self._parse_xml_field(block, "eventType")] if "<eventType>" in block else []),
                        connect_state=self._parse_xml_field(block, "connectState") or "",
                        resource=int(float(self._parse_xml_field(block, "resource") or 0)),
                    )
                )
        return AiResource(
            supported_event_types=supported_types,
            total_occupancy=total,
            channels=channels,
        )

    def query_nvr_face_detection(self, channel: int) -> NvrFaceDetectionConfig:
        """Query the NVR-side face detection ("Enable Detection by NVR") state.

        This is the Detection tab of Function Panel → AI Event → Face
        Recognition — the back-end face detector the recorder runs on the
        selected channel (as opposed to camera-side ``queryVfd``).

        CGI endpoint: ``queryBackFaceMatch``
        """
        self._require_login()
        chl_id = self.channel_guid(channel)
        content = f"<condition><chlId>{chl_id}</chlId></condition><requireField><param/></requireField>"
        data = self._post("queryBackFaceMatch", self._build_request_with_content(content))
        # queryBackFaceMatch omits <status> on success and returns <content>
        # directly; only an explicit fail/errorCode is an error here.
        if "<status>fail</status>" in data or "<errorCode>" in data:
            self._check_response(data, "queryBackFaceMatch")
        item = re.search(r"<item\s+([^>]*)>(.*?)</item>", data, re.DOTALL)
        schedule = ""
        if item:
            sched_m = re.search(r'scheduleGuid="([^"]+)"', item.group(1))
            schedule = sched_m.group(1) if sched_m else ""
        switch = self._parse_xml_field(data, "switch")
        return NvrFaceDetectionConfig(
            chl_id=chl_id,
            enabled=(switch == "true"),
            schedule_id=schedule,
        )

    def set_nvr_face_detection(
        self,
        channel: int,
        enabled: bool,
        *,
        schedule_id: str | None = None,
    ) -> None:
        """Enable/disable NVR-side face detection on a channel.

        Writes the switch read by :meth:`query_nvr_face_detection`. When
        ``schedule_id`` is omitted the channel's current arming schedule is
        preserved.

        CGI endpoint: ``editRealFaceMatch`` (the write paired with the
        ``queryBackFaceMatch`` read).
        """
        self._require_login()
        chl_id = self.channel_guid(channel)
        if schedule_id is None:
            schedule_id = self.query_nvr_face_detection(channel).schedule_id
        sched_attr = f' scheduleGuid="{escape(schedule_id, {chr(34): "&quot;"})}"' if schedule_id else ""
        content = (
            "<content><param><chls>"
            f'<item guid="{chl_id}"{sched_attr}>'
            f"<switch>{'true' if enabled else 'false'}</switch>"
            "</item></chls></param></content>"
        )
        data = self._post("editRealFaceMatch", self._build_request_with_content(content))
        self._check_response(data, "editRealFaceMatch")

    def query_face_match_config(self, channel: int) -> str:
        """Query the face *recognition* (match) config for a channel.

        Returns the raw ``<content>`` XML (match groups, similarity threshold,
        trigger actions) — the shape is firmware-dependent and richer than a
        flat dataclass, so it is returned verbatim for the caller to parse.

        CGI endpoint: ``queryFaceMatchConfig``
        """
        self._require_login()
        chl_id = self.channel_guid(channel)
        content = f"<condition><chlId>{chl_id}</chlId></condition>"
        data = self._post("queryFaceMatchConfig", self._build_request_with_content(content))
        self._check_response(data, "queryFaceMatchConfig")
        body = re.search(r"<content\b[^>]*>.*?</content>", data, re.DOTALL)
        return body.group(0) if body else data

    def query_face_db_groups(self) -> list[FaceDbGroup]:
        """List the face-database groups (allow / reject / limited).

        CGI endpoint: ``queryFacePersonnalInfoGroupList``
        """
        self._require_login()
        data = self._post("queryFacePersonnalInfoGroupList", self._build_request())
        self._check_response(data, "queryFacePersonnalInfoGroupList")
        groups: list[FaceDbGroup] = []
        content = re.search(r"<content\b[^>]*>(.*?)</content>", data, re.DOTALL)
        if content:
            for m in re.finditer(r"<item(\s+[^>]*)?>(.*?)</item>", content.group(1), re.DOTALL):
                attrs = m.group(1) or ""
                block = m.group(2)
                gid = re.search(r'id="([^"]+)"', attrs)
                groups.append(
                    FaceDbGroup(
                        group_id=(gid.group(1) if gid else self._parse_xml_field(block, "id") or ""),
                        name=self._parse_xml_field(block, "name") or "",
                        group_type=self._parse_xml_field(block, "property")
                        or self._parse_xml_field(block, "groupType")
                        or "",
                        face_count=int(self._parse_xml_field(block, "faceNum") or 0),
                    )
                )
        return groups

    def query_face_persons(self, group_id: str, *, page: int = 1, page_size: int = 100) -> list[FacePerson]:
        """List people enrolled in a face-database group.

        CGI endpoint: ``queryFacePersonnalInfoList``. Item fields vary by
        firmware; ``person_id``/``name`` are extracted and the full item block is
        kept in ``FacePerson.extra``.
        """
        self._require_login()
        content = (
            f"<pageIndex>{int(page)}</pageIndex><pageSize>{int(page_size)}</pageSize>"
            '<condition><faceFeatureGroups type="list">'
            f'<item id="{group_id}"></item>'
            "</faceFeatureGroups></condition>"
        )
        data = self._post("queryFacePersonnalInfoList", self._build_request_with_content(content))
        # The web client treats errorCode 536870942/536870947 as "0 people"
        # (an empty group), not a failure.
        err = re.search(r"<errorCode>(.*?)</errorCode>", data)
        if err and err.group(1) in ("536870942", "536870947"):
            return []
        self._check_response(data, "queryFacePersonnalInfoList")
        persons: list[FacePerson] = []
        content_m = re.search(r"<content\b[^>]*>(.*?)</content>", data, re.DOTALL)
        if content_m:
            for m in re.finditer(r'<item\s+id="([^"]+)"[^>]*>(.*?)</item>', content_m.group(1), re.DOTALL):
                pid, block = m.group(1), m.group(2)
                persons.append(
                    FacePerson(
                        person_id=pid,
                        name=self._parse_xml_field(block, "name") or "",
                        group_id=group_id,
                        extra={
                            tag: val
                            for tag, val in re.findall(r"<([a-zA-Z]+)>(.*?)</\1>", block)
                            if tag not in ("name",)
                        },
                    )
                )
        return persons

    def create_face_group(self, name: str, *, group_type: str = "limited") -> None:
        """Create a face-database group (``allow`` / ``reject`` / ``limited``).

        CGI endpoint: ``createFacePersonnalInfoGroup``.
        """
        self._require_login()
        content = (
            "<types><property><enum>allow</enum><enum>reject</enum><enum>limited</enum></property></types>"
            f"<content><name><![CDATA[{name}]]></name>"
            f'<property type="property">{escape(group_type)}</property></content>'
        )
        data = self._post("createFacePersonnalInfoGroup", self._build_request_with_content(content))
        self._check_response(data, "createFacePersonnalInfoGroup")

    def delete_face_groups(self, group_ids: list[str]) -> None:
        """Delete face-database groups by id.

        CGI endpoint: ``delFacePersonnalInfoGroups``.
        """
        self._require_login()
        items = "".join(f'<item id="{gid}"></item>' for gid in group_ids)
        content = f'<condition><ids type="list">{items}</ids></condition>'
        data = self._post("delFacePersonnalInfoGroups", self._build_request_with_content(content))
        self._check_response(data, "delFacePersonnalInfoGroups")

    def get_face_person_image(self, person_id: str, index: int = 0) -> bytes:
        """Fetch an enrolled person's face image (JPEG bytes).

        CGI endpoint: ``requestFacePersonnalInfoImage``. ``index`` selects which
        of the person's enrolled faces. Returns ``b""`` if unavailable.
        """
        self._require_login()
        content = f"<condition><id>{person_id}</id><index>{int(index)}</index></condition>"
        data = self._post("requestFacePersonnalInfoImage", self._build_request_with_content(content))
        self._check_response(data, "requestFacePersonnalInfoImage")
        cdata = re.search(r"<content>\s*<!\[CDATA\[(.*?)\]\]>\s*</content>", data, re.DOTALL)
        return _maybe_b64(cdata.group(1).strip()) if cdata else b""

    def search_face_events(
        self,
        channel: int,
        start: str,
        end: str,
        *,
        similarity: int = 75,
        result_limit: int = 10000,
        fetch_snapshots: bool = False,
    ) -> list[FaceEvent]:
        """Search recorded NVR-side face-detection events for a channel + window.

        ``start``/``end`` are ``YYYY-MM-DD HH:MM:SS`` strings (UTC, matching the
        web client's "By Event" face search). Returns the event index — each
        :class:`FaceEvent` carries ``channel``, ``img_id`` and ``frame_time``;
        pass those to :meth:`get_face_snapshot` for the cropped-face JPEG (stored
        separately on the recorder). Set ``fetch_snapshots=True`` to eagerly
        populate ``FaceEvent.snapshot`` (one extra request per event).

        CGI endpoint: ``searchImageByImageV2``. The compact ``<i>`` records are
        decoded per the web client: field layout
        ``[_, calTimeS, calTimeNS, imgId, channel, …]`` (all hex).
        """
        self._require_login()
        chl_id = self.channel_guid(channel)
        content = (
            f"<resultLimit>{int(result_limit)}</resultLimit>"
            "<condition>"
            f"<startTime>{escape(start)}</startTime>"
            f"<endTime>{escape(end)}</endTime>"
            f'<chls type="list"><item id="{chl_id}"></item></chls>'
            "<event><eventType>byAll</eventType></event>"
            f"<similarity>{int(similarity)}</similarity>"
            "</condition>"
        )
        data = self._post("searchImageByImageV2", self._build_request_with_content(content))
        self._check_response(data, "searchImageByImageV2")
        events: list[FaceEvent] = []
        for rec in re.findall(r"<i>(.*?)</i>", data, re.DOTALL):
            f = rec.split(",")
            if len(f) < 5:
                continue
            try:
                cal_time_s = int(f[1], 16)  # epoch seconds
                cal_time_ns = int(f[2], 16)  # sub-second, 7 digits
                img_id = int(f[3], 16)
                ch = int(f[4], 16)
            except ValueError:
                continue
            frame_time = (
                datetime.fromtimestamp(cal_time_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                + f":{cal_time_ns:07d}"
            )
            ev = FaceEvent(
                chl_id=chl_id,
                channel=ch,
                timestamp=frame_time,
                img_id=img_id,
                frame_time=frame_time,
            )
            if fetch_snapshots:
                ev.snapshot = self.get_face_snapshot(ch, img_id, frame_time)
            events.append(ev)
        return events

    def get_face_snapshot(self, channel: int, img_id: int, frame_time: str) -> bytes:
        """Fetch one face snapshot JPEG by ``img_id`` + ``frame_time``.

        ``img_id`` and ``frame_time`` come from a :class:`FaceEvent` returned by
        :meth:`search_face_events`. Returns the cropped-face JPEG bytes (decoded
        from the device's base64), or ``b""`` if unavailable.

        CGI endpoint: ``requestChSnapFaceImage``.
        """
        self._require_login()
        chl_id = self.channel_guid(channel)
        content = (
            "<condition>"
            f"<imgId>{int(img_id)}</imgId>"
            f"<chlId>{chl_id}</chlId>"
            f"<frameTime>{escape(frame_time)}</frameTime>"
            "<featureStatus>false</featureStatus>"
            "</condition>"
        )
        data = self._post("requestChSnapFaceImage", self._build_request_with_content(content))
        self._check_response(data, "requestChSnapFaceImage")
        cdata = re.search(r"<content>\s*<!\[CDATA\[(.*?)\]\]>\s*</content>", data, re.DOTALL)
        return _maybe_b64(cdata.group(1).strip()) if cdata else b""

    # ── Alarm Server (event push target) ─────────────────────────────

    def query_alarm_server(self) -> AlarmServerConfig:
        """Query the NVR's Alarm Server push configuration.

        The Alarm Server action pushes alarm frames (incl. AI/face events, when
        their type code is in ``alarm_types``) to an external listener. Pair the
        returned target with :class:`~pytvt.alarm_server.AlarmServer` to receive
        them.

        CGI endpoint: ``queryAlarmServerParam``
        """
        self._require_login()
        data = self._post("queryAlarmServerParam", self._build_request())
        self._check_response(data, "queryAlarmServerParam")
        content = re.search(r"<content\b[^>]*>(.*?)</content>", data, re.DOTALL)
        block = content.group(1) if content else data
        types_raw = self._parse_xml_field(block, "alarmServerAlarmTypes") or ""
        alarm_types = [int(t) for t in types_raw.split(",") if t.strip().isdigit()]
        heartbeat = re.search(r"<heartbeat>(.*?)</heartbeat>", block, re.DOTALL)
        hb = heartbeat.group(1) if heartbeat else ""
        return AlarmServerConfig(
            enabled=self._parse_xml_field(block, "switch") == "true",
            address=self._parse_xml_field(block, "address") or "",
            url=self._parse_xml_field(block, "url") or "",
            port=int(self._parse_xml_field(block, "port") or 80),
            data_format=self._parse_xml_field(block, "dataFormat") or "XML",
            schedule_id=self._parse_xml_field(block, "alarmServerSchedule") or "",
            alarm_types=alarm_types,
            device_id=self._parse_xml_field(block, "deviceId") or "",
            token=self._parse_xml_field(block, "token") or "",
            heartbeat_enabled=(self._parse_xml_field(hb, "switch") == "true") if hb else False,
            heartbeat_interval=int(self._parse_xml_field(hb, "interval") or 10) if hb else 10,
        )

    def set_alarm_server(self, config: AlarmServerConfig) -> None:
        """Write the NVR's Alarm Server push configuration.

        Read-modify-write: fetch with :meth:`query_alarm_server`, adjust the
        target/enable/alarm_types, and pass the config here. To point the NVR at
        a receiver: set ``address``/``port`` (and ``url`` if used), ``enabled=True``,
        and ensure the desired event codes are in ``alarm_types`` (e.g. ``16`` for
        face match).

        ``alarm_types`` is only sent when ``data_format == "XML"`` (matches the
        web client). CGI endpoint: ``editAlarmServerParam``.
        """
        self._require_login()
        parts = [
            "<content>",
            f"<address>{escape(config.address)}</address>",
            f"<url>{escape(config.url)}</url>",
            f"<switch>{'true' if config.enabled else 'false'}</switch>",
            f"<dataFormat>{escape(config.data_format)}</dataFormat>",
            f"<port>{int(config.port)}</port>",
            f"<alarmServerSchedule>{escape(config.schedule_id)}</alarmServerSchedule>",
        ]
        if config.data_format == "XML":
            parts.append(
                f"<alarmServerAlarmTypes>{','.join(str(t) for t in config.alarm_types)}</alarmServerAlarmTypes>"
            )
        parts.append(
            "<heartbeat>"
            f"<switch>{'true' if config.heartbeat_enabled else 'false'}</switch>"
            f"<interval>{int(config.heartbeat_interval)}</interval>"
            "</heartbeat>"
        )
        parts.append("</content>")
        data = self._post("editAlarmServerParam", self._build_request_with_content("".join(parts)))
        self._check_response(data, "editAlarmServerParam")


def _maybe_b64(value: str) -> bytes:
    """Decode a base64 image field to bytes; empty/invalid → ``b""``."""
    if not value:
        return b""
    try:
        # binascii.Error (raised on bad base64) subclasses ValueError.
        return base64.b64decode(value, validate=False)
    except (ValueError, TypeError):
        return b""


def _ffmpeg_rtsp_frame_args(rtsp_url: str, timeout: int) -> list[str]:
    """Shared ffmpeg argument list for grabbing one JPEG frame from RTSP."""
    return [
        "ffmpeg",
        "-y",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        str(timeout * 1_000_000),
        "-i",
        rtsp_url,
        "-frames:v",
        "1",
        "-q:v",
        "2",
    ]


def rtsp_snapshot(rtsp_url: str, output_path: str, timeout: int = 10) -> bool:
    """Capture a single JPEG frame from an RTSP stream to a file using ffmpeg.

    Args:
        rtsp_url: Full RTSP URL (with credentials)
        output_path: Output JPEG file path
        timeout: ffmpeg timeout in seconds

    Returns:
        True if snapshot was saved successfully
    """
    import os

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    try:
        result = subprocess.run(
            [*_ffmpeg_rtsp_frame_args(rtsp_url, timeout), output_path],
            capture_output=True,
            timeout=timeout + 5,
        )
        return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def rtsp_snapshot_bytes(rtsp_url: str, timeout: int = 10) -> bytes | None:
    """Capture a single JPEG frame from an RTSP stream as bytes (ffmpeg -> stdout).

    The byte-returning sibling of :func:`rtsp_snapshot` — same ffmpeg frame-grab,
    but written to ``pipe:1`` so callers that want a JPEG in memory (e.g. the
    device-SDK ``snapshot`` preferred path) avoid a temp file.

    Returns the JPEG bytes, or ``None`` on any failure (non-zero exit, empty
    output, ffmpeg missing, or timeout).
    """
    try:
        result = subprocess.run(
            [*_ffmpeg_rtsp_frame_args(rtsp_url, timeout), "-f", "image2", "pipe:1"],
            capture_output=True,
            timeout=timeout + 5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode == 0 and result.stdout:
        return result.stdout
    return None


def main():
    """CLI: query or enable NVR services."""
    import argparse

    parser = argparse.ArgumentParser(description="TVT NVR Web API client")
    parser.add_argument("host", help="NVR IP address")
    parser.add_argument("-u", "--username", default="admin")
    parser.add_argument("-p", "--password", default="admin")
    parser.add_argument("--port", type=int, default=80, help="HTTP port (default: 80)")
    parser.add_argument("--timeout", type=int, default=10)

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show RTSP/API server status")
    sub.add_parser("enable-rtsp", help="Enable RTSP server")
    sub.add_parser("enable-api", help="Enable API server")
    sub.add_parser("enable-all", help="Enable both RTSP and API server")
    sub.add_parser("ports", help="Show port configuration")
    sub.add_parser("channels", help="List all camera channels")

    snap_parser = sub.add_parser("snapshot", help="Capture snapshot from a channel")
    snap_parser.add_argument("channel", type=int, help="Channel number (1-indexed)")
    snap_parser.add_argument("-o", "--output", help="Output JPEG path (default: snapshot_CH.jpg)")
    snap_parser.add_argument("--stream", choices=["main", "sub"], default="main")

    snap_all_parser = sub.add_parser("snapshot-all", help="Capture snapshots from all online channels")
    snap_all_parser.add_argument("-d", "--output-dir", default="snapshots", help="Output directory")
    snap_all_parser.add_argument("--stream", choices=["main", "sub"], default="main")

    sub.add_parser("users", help="List NVR users")
    sub.add_parser("password-security", help="Show password security settings")

    chpw_parser = sub.add_parser("change-password", help="Change NVR admin password")
    chpw_parser.add_argument("new_password", help="New password")

    sync_parser = sub.add_parser("sync-credentials", help="Update stored IPC credentials on the NVR")
    sync_parser.add_argument("--device-user", default="admin", help="Username to set for IPC devices")
    sync_parser.add_argument("--device-password", help="Password to set (default: NVR password)")

    set_admin_email_parser = sub.add_parser("set-admin-email", help="Set/reset email for admin account")
    set_admin_email_parser.add_argument("--email", required=True, help="Email value to store")

    set_email_parser = sub.add_parser("set-user-email", help="Set/reset email for a user account")
    set_email_parser.add_argument("--target-user", default="admin", help="User account to update")
    set_email_parser.add_argument("--email", required=True, help="Email value to store")

    chall_parser = sub.add_parser(
        "change-admin-and-sync", help="Change admin password and update all stored IPC credentials"
    )
    chall_parser.add_argument("new_password", help="New password")

    sub.add_parser("ai-resource", help="Show NVR AI-compute pool and per-channel allocation")
    fd_parser = sub.add_parser("face-detection", help="Show NVR-side face detection state for a channel")
    fd_parser.add_argument("channel", type=int, help="Channel number (1-indexed)")
    sub.add_parser("face-db", help="List face-database groups")
    sub.add_parser("alarm-server", help="Show Alarm Server push configuration")

    args = parser.parse_args()

    with NvrClient(args.host, args.username, args.password, port=args.port, timeout=args.timeout) as nvr:
        try:
            nvr.login()
        except NvrApiError as e:
            print(f"Login failed: {e}", file=sys.stderr)
            sys.exit(1)

        if args.command == "status":
            rtsp = nvr.query_rtsp_server()
            api = nvr.query_api_server()
            print(
                f"RTSP Server:  {'ENABLED' if rtsp.enabled else 'DISABLED'}  (port={rtsp.port}, auth={rtsp.auth_type})"
            )
            print(f"API Server:   {'ENABLED' if api.enabled else 'DISABLED'}  (auth={api.auth_type})")

        elif args.command == "enable-rtsp":
            nvr.enable_rtsp()
            print("RTSP server enabled.")

        elif args.command == "enable-api":
            nvr.enable_api_server()
            print("API server enabled.")

        elif args.command == "enable-all":
            changed = nvr.ensure_services_enabled()
            if changed:
                print(f"Enabled: {', '.join(changed.keys())}")
            else:
                print("Both services already enabled.")

        elif args.command == "ports":
            ports = nvr.query_port_config()
            print(f"HTTP:        {ports.http_port}")
            print(f"HTTPS:       {ports.https_port}")
            print(f"Server:      {ports.server_port}")
            print(f"RTSP:        {ports.rtsp_port}")
            print(f"POS:         {ports.pos_port}")
            print(f"Auto Report: {ports.auto_report_port}")

        elif args.command == "channels":
            channels = nvr.query_channels()
            for ch in channels:
                status = "ONLINE" if ch.online else "OFFLINE"
                print(f"  CH{ch.chl_num:>2}  {status:<7}  {ch.ip:<15}  {ch.name}  ({ch.model})")

        elif args.command == "snapshot":
            nvr.ensure_services_enabled()
            output = args.output or f"snapshot_{args.channel}.jpg"
            print(f"Capturing CH{args.channel} -> {output} ...")
            ok = nvr.snapshot(args.channel, output, stream_type=args.stream)
            if ok:
                import os

                size = os.path.getsize(output)
                print(f"OK ({size:,} bytes)")
            else:
                print("FAILED", file=sys.stderr)
                sys.exit(1)

        elif args.command == "snapshot-all":
            import os

            nvr.ensure_services_enabled()
            channels = nvr.query_channels()
            online = [ch for ch in channels if ch.online]
            print(f"Capturing {len(online)} online channels ...")
            os.makedirs(args.output_dir, exist_ok=True)
            ok_count = 0
            for ch in online:
                fname = f"{args.host}_ch{ch.chl_num}_{ch.name}.jpg".replace(" ", "_")
                path = os.path.join(args.output_dir, fname)
                print(f"  CH{ch.chl_num:>2} {ch.name:<20} ", end="", flush=True)
                if nvr.snapshot(ch.chl_num, path, stream_type=args.stream):
                    size = os.path.getsize(path)
                    print(f"OK ({size:,} bytes)")
                    ok_count += 1
                else:
                    print("FAILED")
            print(f"\nDone: {ok_count}/{len(online)} snapshots captured in {args.output_dir}/")

        elif args.command == "users":
            users = nvr.query_users()
            for u in users:
                status = "enabled" if u.enabled else "disabled"
                print(f"  {u.username:<20} type={u.user_type:<15} {status}  id={u.user_id}")

        elif args.command == "password-security":
            sec = nvr.query_password_security()
            print(f"Min strength:    {sec.min_strength}")
            print(f"Expiration:      {sec.expiration_days} days (0=never)")
            print(f"Allowed levels:  {', '.join(sec.allowed_levels)}")

        elif args.command == "change-password":
            nvr.change_own_password(args.password, args.new_password)
            print("Password changed. Re-logging in ...")
            nvr.password = args.new_password
            nvr.login()
            print("OK - logged in with new password.")

        elif args.command == "sync-credentials":
            pwd = args.device_password or args.password
            count = nvr.update_device_credentials(username=args.device_user, password=pwd)
            print(f"Updated stored credentials for {count} devices.")

        elif args.command == "set-admin-email":
            result = nvr.set_admin_email(email=args.email)
            print(
                f"User {result['username']} (id={result['user_id']}) email: "
                f"{result['old_email'] or '<empty>'} -> {result['new_email']}"
            )

        elif args.command == "set-user-email":
            result = nvr.set_user_email(username=args.target_user, email=args.email)
            print(
                f"User {result['username']} (id={result['user_id']}) email: "
                f"{result['old_email'] or '<empty>'} -> {result['new_email']}"
            )

        elif args.command == "change-admin-and-sync":
            result = nvr.change_admin_password_and_sync(args.password, args.new_password)
            print(f"Password changed. Updated {result['devices_updated']} device credentials.")
            print("Re-login OK. New password is active.")

        elif args.command == "ai-resource":
            res = nvr.query_ai_resource()
            print(f"AI event types: {', '.join(res.supported_event_types) or '(none)'}")
            print(f"Pool occupancy: {res.total_occupancy:.0f}%")
            for chl in res.channels:
                types = ", ".join(chl.event_types) or "-"
                print(f"  {chl.chl_id}  {chl.name:<24} state={chl.connect_state:<6} events={types}")

        elif args.command == "face-detection":
            cfg = nvr.query_nvr_face_detection(args.channel)
            print(f"Channel {args.channel} ({cfg.chl_id})")
            print(f"  NVR-side face detection: {'ENABLED' if cfg.enabled else 'DISABLED'}")
            print(f"  Schedule: {cfg.schedule_id or '(none)'}")

        elif args.command == "face-db":
            groups = nvr.query_face_db_groups()
            if not groups:
                print("No face-database groups.")
            for g in groups:
                print(f"  {g.group_id}  {g.name:<24} type={g.group_type:<8} faces={g.face_count}")

        elif args.command == "alarm-server":
            cfg = nvr.query_alarm_server()
            print(f"Alarm Server: {'ENABLED' if cfg.enabled else 'DISABLED'}")
            print(f"  Target: {cfg.address or '(unset)'}:{cfg.port}{cfg.url}  format={cfg.data_format}")
            print(f"  Pushed alarm types: {','.join(str(t) for t in cfg.alarm_types) or '(none)'}")
            print(f"  Heartbeat: {'on' if cfg.heartbeat_enabled else 'off'} ({cfg.heartbeat_interval}s)")


if __name__ == "__main__":
    main()
