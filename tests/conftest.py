"""Shared fixtures for the pytvt test suite."""

from __future__ import annotations

import pytest

from pytvt.models import CameraInfo, DeviceEntry, ScannerConfig, ScanResult


@pytest.fixture()
def default_config() -> ScannerConfig:
    return ScannerConfig(
        username="admin",
        password="test123",
        port=6036,
        timeout=5,
        max_channels=64,
        concurrency=2,
        api_url="http://localhost:3000",
    )


@pytest.fixture()
def sample_device() -> DeviceEntry:
    return DeviceEntry(
        ip="10.0.0.1",
        site="Test Site",
        hostname="NVR-01",
        mac="58:5B:69:AA:BB:CC",
        port=0,
        manufacturer="TVT",
    )


@pytest.fixture()
def sample_camera() -> CameraInfo:
    return CameraInfo(
        channel=1,
        name="Lobby",
        address="192.168.1.100",
        port=9008,
        status="Online",
        protocol="TVT",
        model="TD-9544S4",
    )


@pytest.fixture()
def successful_result(
    sample_device: DeviceEntry, sample_camera: CameraInfo, default_config: ScannerConfig
) -> ScanResult:
    r = ScanResult.for_device(sample_device, default_config, backend="protocol")
    r.success = True
    r.device_name = "NVR-01"
    r.device_model = "TD-3332B4"
    r.serial_number = "ABC123"
    r.firmware = "5.2.3.190"
    r.total_channels = 4
    r.cameras = [sample_camera]
    return r


@pytest.fixture()
def failed_result(sample_device: DeviceEntry, default_config: ScannerConfig) -> ScanResult:
    r = ScanResult.for_device(sample_device, default_config, backend="protocol")
    r.error = "Connection timed out (5s)"
    return r


# ── Sample protocol data ────────────────────────────────────────────


@pytest.fixture()
def standard_init_packet() -> bytes:
    """A 64-byte standard init packet (flag='1111', protocolVer=3, loginEncrypt=2)."""
    import struct

    data = bytearray(64)
    data[0:4] = b"1111"  # flag
    struct.pack_into("<I", data, 4, 1)  # devType
    struct.pack_into("<I", data, 12, 3)  # protocolVer = 3 (standard)
    struct.pack_into("<I", data, 24, 0)  # encryptType
    data[32:38] = bytes([0x58, 0x5B, 0x69, 0xAA, 0xBB, 0xCC])  # MAC
    data[44] = 2  # loginEncrypt = 2 (XOR)
    data[45:48] = bytes([0x11, 0x22, 0x33])  # loginNonce
    return bytes(data)


@pytest.fixture()
def head_init_packet() -> bytes:
    """A 64-byte head-variant init packet (flag='head', protocolVer=11)."""
    import struct

    data = bytearray(64)
    data[0:4] = b"head"  # flag
    struct.pack_into("<I", data, 4, 1)  # devType
    struct.pack_into("<I", data, 12, 11)  # protocolVer = 11 (head variant)
    data[32:38] = bytes([0x58, 0x5B, 0x69, 0xDD, 0xEE, 0xFF])
    data[44] = 2  # loginEncrypt
    data[45:48] = bytes([0xAA, 0xBB, 0xCC])  # loginNonce
    return bytes(data)


# ── Sample XML for discovery ────────────────────────────────────────


DISCOVERY_XML = b"""\
<multicastSearchResult>
  <tcpIp>
    <devName>NVR-Front</devName>
    <ipAddr>192.168.1.50</ipAddr>
    <mask>255.255.255.0</mask>
    <maskAddr>255.255.255.0</maskAddr>
    <gateway>192.168.1.1</gateway>
    <dns1>8.8.8.8</dns1>
    <dns2>8.8.4.4</dns2>
    <macAddr>58:5B:69:11:22:33</macAddr>
  </tcpIp>
  <port>
    <dataPort>9008</dataPort>
    <httpPort>80</httpPort>
  </port>
  <productInfo>
    <devName>NVR</devName>
    <productModel>TD-3332B4</productModel>
    <productSeries>N9000</productSeries>
    <softwareVer>V5.2.0</softwareVer>
    <kernelVer>3.18.20</kernelVer>
  </productInfo>
</multicastSearchResult>
"""
