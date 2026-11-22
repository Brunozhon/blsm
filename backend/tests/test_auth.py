from blsm.auth import SessionManager, hash_password, verify_password


def test_password_hash_round_trip() -> None:
    encoded = hash_password("a sufficiently long password")
    assert verify_password("a sufficiently long password", encoded)
    assert not verify_password("the wrong password", encoded)
    assert "sufficiently" not in encoded


def test_signed_session_rejects_tampering() -> None:
    manager = SessionManager(b"x" * 32)
    token = manager.create()
    assert manager.verify(token) is not None
    assert manager.verify(token + "tampered") is None

