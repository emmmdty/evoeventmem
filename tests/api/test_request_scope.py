from __future__ import annotations

import pytest

from evoeventmem.core.ports import RequestScope, ScopeMismatch


def test_scope_requires_nonempty_tenant() -> None:
    with pytest.raises(ValueError):
        RequestScope(tenant_id="", user_id="user-1")
    with pytest.raises(ValueError):
        RequestScope(tenant_id="   ", user_id="user-1")
    with pytest.raises(ValueError):
        RequestScope(user_id="user-1")


def test_scope_requires_nonempty_user() -> None:
    with pytest.raises(ValueError):
        RequestScope(tenant_id="tenant-1", user_id="")
    with pytest.raises(ValueError):
        RequestScope(tenant_id="tenant-1", user_id="   ")
    with pytest.raises(ValueError):
        RequestScope(tenant_id="tenant-1")


def test_scope_accepts_optional_session_narrowing() -> None:
    assert RequestScope(tenant_id="t", user_id="u").session_id is None
    assert RequestScope(tenant_id="t", user_id="u", session_id="s").session_id == "s"


def test_scope_canonical_serialization_is_stable() -> None:
    scope = RequestScope(tenant_id="tenant-1", user_id="user-1", session_id="session-9")
    assert scope.canonical_key() == "tenant-1|user-1|session-9"


def test_scope_canonical_serialization_omits_missing_session() -> None:
    scope = RequestScope(tenant_id="tenant-1", user_id="user-1")
    assert scope.canonical_key() == "tenant-1|user-1"


def test_scope_canonical_serialization_is_deterministic() -> None:
    first = RequestScope(tenant_id="t", user_id="u", session_id="s").canonical_key()
    second = RequestScope(tenant_id="t", user_id="u", session_id="s").canonical_key()
    assert first == second


def test_scope_matches_when_identities_agree() -> None:
    scope = RequestScope(tenant_id="t", user_id="u", session_id="s")
    assert scope.mismatch(tenant_id="t", user_id="u", session_id="s") is None


def test_scope_mismatch_reports_user_difference() -> None:
    scope = RequestScope(tenant_id="t", user_id="u", session_id="s")
    mismatch = scope.mismatch(tenant_id="t", user_id="other", session_id="s")
    assert isinstance(mismatch, ScopeMismatch)
    assert mismatch.field == "user_id"
    assert mismatch.scope_value == "u"
    assert mismatch.body_value == "other"


def test_scope_mismatch_reports_tenant_difference() -> None:
    scope = RequestScope(tenant_id="t", user_id="u")
    mismatch = scope.mismatch(tenant_id="other", user_id="u")
    assert isinstance(mismatch, ScopeMismatch)
    assert mismatch.field == "tenant_id"


def test_scope_mismatch_reports_session_difference() -> None:
    scope = RequestScope(tenant_id="t", user_id="u", session_id="s")
    mismatch = scope.mismatch(tenant_id="t", user_id="u", session_id="other")
    assert isinstance(mismatch, ScopeMismatch)
    assert mismatch.field == "session_id"