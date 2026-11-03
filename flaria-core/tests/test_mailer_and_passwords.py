import pytest

from app.api.auth import hash_reset_token
from app.core.config import Settings
from app.schemas import ChangePasswordRequest, PasswordResetConfirm
from app.services.mailer import Mailer, password_changed_mail, reset_mail


def test_reset_mail_contents_and_escaping():
    url = "https://flaria.example.org/reset-password#token=abc_DEF-123"
    m = reset_mail("p@example.org", "<script>Eve</script>", url, 30, by_admin=False)
    assert m.subject == "Reset your Flaria password"
    assert url in m.text and "30 minutes" in m.text
    assert "<script>" not in m.html and "&lt;script&gt;" in m.html  # names are escaped in HTML
    assert "abc_DEF-123" in m.html
    assert "administrator" in reset_mail("p@example.org", "P", url, 30, by_admin=True).text


def test_changed_mail_and_mime_structure():
    m = password_changed_mail("p@example.org", "Pat", "https://flaria.example.org", via_reset=True)
    msg = Mailer(Settings())._message(m)
    assert msg.is_multipart()
    assert {p.get_content_type() for p in msg.iter_parts()} == {"text/plain", "text/html"}
    assert msg["Auto-Submitted"] == "auto-generated"
    assert "forgot-password" in m.text


def test_reset_token_hash_is_stable_sha256():
    h = hash_reset_token("token-value")
    assert len(h) == 64 and h == hash_reset_token("token-value") and h != hash_reset_token("token-valuf")


def test_password_policy_shared_by_change_and_reset():
    with pytest.raises(ValueError):
        ChangePasswordRequest(current_password="x", new_password="alllowercaseletters")
    with pytest.raises(ValueError):
        PasswordResetConfirm(token="a" * 43, new_password="Short-1")
    with pytest.raises(ValueError):
        PasswordResetConfirm(token="has spaces in it " * 3, new_password="Good-Password-123")
    assert PasswordResetConfirm(token="a" * 43, new_password="Good-Password-123")


@pytest.mark.parametrize("override, message", [
    ({"mail_backend": "console"}, "MAIL_BACKEND"),
    ({"public_app_url": "http://flaria.example.org"}, "PUBLIC_APP_URL"),
])
def test_production_refuses_unsafe_mail_settings(override, message):
    base = {"env": "production", "jwt_secret": "x" * 64, "cookie_secure": True, "mail_backend": "smtp",
            "public_app_url": "https://flaria.example.org", "cors_origins": ["https://flaria.example.org"]}
    with pytest.raises(ValueError, match=message):
        Settings(**{**base, **override})
