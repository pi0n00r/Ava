import importlib
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def _reload_auth(monkeypatch, jwt_secret):
    if jwt_secret is None:
        monkeypatch.delenv("JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET", jwt_secret)

    import auth  # noqa: E402

    return importlib.reload(auth)


def test_jwt_secret_unset_uses_default_dev_secret(monkeypatch) -> None:
    auth = _reload_auth(monkeypatch, None)
    assert auth.SECRET_KEY == auth.DEFAULT_DEV_SECRET
    assert auth.USING_PLACEHOLDER_SECRET is True


def test_jwt_secret_empty_uses_default_dev_secret(monkeypatch) -> None:
    auth = _reload_auth(monkeypatch, "")
    assert auth.SECRET_KEY == auth.DEFAULT_DEV_SECRET
    assert auth.USING_PLACEHOLDER_SECRET is True


def test_jwt_secret_change_me_please_marked_placeholder(monkeypatch) -> None:
    auth = _reload_auth(monkeypatch, "change-me-please")
    assert auth.SECRET_KEY == "change-me-please"
    assert auth.USING_PLACEHOLDER_SECRET is True


def test_jwt_secret_custom_not_placeholder(monkeypatch) -> None:
    auth = _reload_auth(monkeypatch, "super-secret-value-123")
    assert auth.SECRET_KEY == "super-secret-value-123"
    assert auth.USING_PLACEHOLDER_SECRET is False

