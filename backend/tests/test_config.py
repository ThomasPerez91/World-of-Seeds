from pydantic import SecretStr
from sqlalchemy import make_url

from app.core.config import Settings


def test_database_url_handles_reserved_password_characters() -> None:
    settings = Settings(database_url=None, postgres_password=SecretStr("p@ss:/#word"))

    url = make_url(settings.sqlalchemy_database_url)

    assert url.password == "p@ss:/#word"
    assert url.drivername == "postgresql+asyncpg"


def test_cookie_is_not_secure_for_the_initial_ssh_tunnel() -> None:
    settings = Settings()

    assert settings.cookie_secure is False
