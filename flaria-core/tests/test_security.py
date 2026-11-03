import uuid

import pytest

from app.core.security import (
    Role, TokenError, create_access_token, create_refresh_token, decode_token, hash_password, verify_password,
)


def test_argon2id_roundtrip():
    h = hash_password("Correct-Horse-9")
    assert h.startswith("$argon2id$")
    ok, rehash = verify_password("Correct-Horse-9", h)
    assert ok and rehash is None
    assert verify_password("wrong", h) == (False, None)
    assert verify_password("anything", None) == (False, None)


def test_token_types_are_enforced():
    uid = uuid.uuid4()
    access = create_access_token(uid, Role.CLINICIAN, 3)
    claims = decode_token(access.token, "access")
    assert claims["sub"] == str(uid) and claims["role"] == "CLINICIAN" and claims["ver"] == 3
    with pytest.raises(TokenError):
        decode_token(access.token, "refresh")
    refresh = create_refresh_token(uid, uuid.uuid4(), 3)
    with pytest.raises(TokenError):
        decode_token(refresh.token, "access")


def test_tampered_token_rejected():
    tok = create_access_token(uuid.uuid4(), Role.PATIENT, 0).token
    head, body, sig = tok.split(".")
    with pytest.raises(TokenError):
        decode_token(f"{head}.{body}.{sig[:-2]}AA", "access")
