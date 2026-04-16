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
import sys
from dataclasses import dataclass, field

from ._crypto import aes_ecb_zeropad
from .models import (
    ApiServerConfig,
    Channel,
    NvrLanFreeDevice,
    NvrApiError,
    PasswordSecurity,
    PortConfig,
    RtspServerConfig,
    User,
)

XML_HEADER = '<?xml version="1.0" encoding="utf-8" ?>'
SYSTEM_TYPE = "NVMS-9000"


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
            msg = f"NVR API error"
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

        Uses challenge-response: reqLogin → hash → doLogin.

        Supports two firmware variants:
        - Older firmware returns both ``nonce`` and ``token`` in reqLogin.
        - Newer firmware (1.4.12+) returns only ``nonce`` and ``sessionId``;
          the token is returned by doLogin instead.
        """
        # Step 1: Request login challenge
        req_body = self._build_request()
        data = self._post("reqLogin", req_body)

        nonce = self._parse_xml_field(data, "nonce")
        token = self._parse_xml_field(data, "token")

        if not nonce:
            # Check for specific error codes
            error_code = self._parse_xml_field(data, "errorCode")
            raise NvrApiError(
                f"reqLogin failed: no nonce in response (errorCode={error_code})",
                error_code,
            )

        # Some firmware versions don't return a token in reqLogin;
        # doLogin will return it instead. Use "null" as placeholder.
        self._token = token  # may be None

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

    def query_channels(self) -> list[Channel]:
        """Query the list of cameras/channels connected to the NVR."""
        self._require_login()
        dev_data = self._post("queryDevList", self._build_request())
        self._check_response(dev_data, "queryDevList")

        # Get online channel IDs
        online_data = self._post("queryOnlineChlList", self._build_request())
        online_ids: set[str] = set()
        if "<status>success</status>" in online_data:
            online_ids = set(re.findall(r'<item\s+id="([^"]+)"', online_data))

        channels: list[Channel] = []
        for m in re.finditer(r'<item\s+id="([^"]+)">(.*?)</item>', dev_data, re.DOTALL):
            dev_id = m.group(1)
            block = m.group(2)
            channels.append(
                Channel(
                    chl_num=int(self._parse_xml_field(block, "chlNum") or 0),
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
                        True
                        if activate_status == "ACTIVATED"
                        else False
                        if activate_status == "UNACTIVATED"
                        else None
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
        body = (
            self._build_request_with_content(
                "<content><device><item id='1'>"
                f"<oldIP>{old_ip}</oldIP>"
                f"<newIP>{new_ip}</newIP>"
                f"<netmask>{netmask}</netmask>"
                f"<gateway>{gateway}</gateway>"
                f"<username>{username}</username>"
                f"<password{attrs}><![CDATA[{encrypted_password}]]></password>"
                "</item></device></content>"
            )
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

        body = self._build_request_with_content(
            f'<condition><devIds type="list">{"".join(items)}</devIds></condition>'
        )
        data = self._post("delDevList", body)
        self._check_response(data, "delDevList")
        return len(dev_ids)

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
            raise NvrApiError(
                f"Failed to change IPC password for {len(failures)} channel(s): {', '.join(failures)}"
            )
        return updated

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


def rtsp_snapshot(rtsp_url: str, output_path: str, timeout: int = 10) -> bool:
    """Capture a single JPEG frame from an RTSP stream using ffmpeg.

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
            [
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
                output_path,
            ],
            capture_output=True,
            timeout=timeout + 5,
        )
        return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


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

    chall_parser = sub.add_parser(
        "change-admin-and-sync", help="Change admin password and update all stored IPC credentials"
    )
    chall_parser.add_argument("new_password", help="New password")

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
            print(f"Password changed. Re-logging in ...")
            nvr.password = args.new_password
            nvr.login()
            print("OK - logged in with new password.")

        elif args.command == "sync-credentials":
            pwd = args.device_password or args.password
            count = nvr.update_device_credentials(username=args.device_user, password=pwd)
            print(f"Updated stored credentials for {count} devices.")

        elif args.command == "change-admin-and-sync":
            result = nvr.change_admin_password_and_sync(args.password, args.new_password)
            print(f"Password changed. Updated {result['devices_updated']} device credentials.")
            print(f"Re-login OK. New password is active.")


if __name__ == "__main__":
    main()
