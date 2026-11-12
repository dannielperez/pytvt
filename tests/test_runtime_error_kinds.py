from __future__ import annotations

import pytest

import pytvt
from pytvt.runtime_client import (
    RuntimeErrorKind,
    RuntimeOperationTimeoutError,
    RuntimeRemoteError,
)

# Kinds pytvt-runtime sends today (server/runtime/worker). Consumers map these
# instead of re-typing wire strings.
WIRE_KINDS = {
    "login_credential",
    "login_transient",
    "login_cooldown",
    "operation_error",
    "operation_timeout",
    "unsupported_operation",
    "capability_unavailable",
    "busy",
    "queue_timeout",
    "client_timeout",
    "aborted",
    "closed",
    "worker_error",
    "runtime_error",
    "invalid_request",
    "protocol_mismatch",
}


def test_enum_covers_exactly_the_runtime_wire_kinds() -> None:
    assert {member.value for member in RuntimeErrorKind} == WIRE_KINDS


def test_members_compare_equal_to_wire_strings() -> None:
    exc = RuntimeRemoteError("login_transient", "Login failed")
    assert exc.kind == RuntimeErrorKind.LOGIN_TRANSIENT
    assert RuntimeErrorKind("queue_timeout") is RuntimeErrorKind.QUEUE_TIMEOUT


@pytest.mark.parametrize("kind", sorted(WIRE_KINDS))
def test_only_an_explicit_rejection_is_credential_rejected(kind: str) -> None:
    exc = RuntimeRemoteError(kind, "runtime failure")
    assert exc.credential_rejected is (kind == "login_credential")


def test_unknown_future_kind_is_not_a_rejection() -> None:
    exc = RuntimeRemoteError("some_future_kind", "newer runtime")
    assert exc.credential_rejected is False
    assert exc.kind == "some_future_kind"


def test_timeout_kind_stays_a_plain_wire_string() -> None:
    exc = RuntimeOperationTimeoutError("deadline")
    assert exc.kind == RuntimeErrorKind.OPERATION_TIMEOUT
    assert type(exc.kind) is str


def test_kind_enum_is_exported_from_the_package() -> None:
    assert pytvt.RuntimeErrorKind is RuntimeErrorKind
    assert "RuntimeErrorKind" in pytvt.__all__
