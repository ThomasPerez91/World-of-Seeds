from pydantic import SecretStr
from sqlalchemy import make_url

from app.core.config import Settings


def test_database_url_handles_reserved_password_characters() -> None:
    settings = Settings(postgres_password=SecretStr("p@ss:/#word"))

    url = make_url(settings.sqlalchemy_database_url)

    assert url.password == "p@ss:/#word"
    assert url.drivername == "postgresql+asyncpg"
