import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.coordination import RedisCoordinator
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.integrations import ExternalServicesMonitor
from app.integrations.newgreedy_config import NewGreedyConfigStore
from app.integrations.newgreedy_restart import NewGreedyRestartStore
from app.integrations.wos_restart import WosRestartStore
from app.main import app
from app.models import Base
from app.options import OptionsStore


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    return root


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, data_root: Path) -> AsyncIterator[AsyncClient]:
    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    test_settings = Settings(data_root=data_root)

    def override_settings() -> Settings:
        return test_settings

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = override_settings
    app.state.external_services_monitor = ExternalServicesMonitor(test_settings)
    app.state.redis_coordinator = RedisCoordinator.unconfigured()
    app.state.newgreedy_config_store = NewGreedyConfigStore(
        test_settings.data_root,
        max_bytes=test_settings.newgreedy_config_max_bytes,
    )
    app.state.newgreedy_restart_store = NewGreedyRestartStore(
        test_settings.data_root,
        status_owner_uid=os.geteuid(),
    )
    app.state.wos_restart_store = WosRestartStore(
        test_settings.data_root,
        status_owner_uid=os.geteuid(),
    )
    app.state.options_store = OptionsStore(test_settings.data_root)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()
