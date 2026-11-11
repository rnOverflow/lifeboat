from __future__ import annotations

import os
import secrets
import shutil
import tempfile

import pytest

os.environ.setdefault("LIFEBOAT_BCRYPT_ROUNDS", "4")

from app.clock import FakeClock
from app.config import Settings
from app.db import Database
from app.engine.runtime import Runtime

TEST_DB = os.environ.get("LIFEBOAT_TEST_DATABASE_URL", "postgresql+psycopg://lifeboat@localhost:5432/lifeboat_test")


@pytest.fixture(scope="session")
def database() -> Database:
    db = Database(TEST_DB)
    db.drop_all()
    db.create_all()
    return db


@pytest.fixture
def backup_dir():
    d = tempfile.mkdtemp(prefix="lifeboat-backups-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def settings(backup_dir) -> Settings:
    return Settings(env="test", database_url=TEST_DB, scheduler_enabled=False, jwt_secret=secrets.token_urlsafe(32),
                    sim_replication_delay_s=2.0, sim_write_interval_s=1.0, backup_dir=backup_dir,
                    detection_threshold=3, recovery_threshold=2, _env_file=None)


@pytest.fixture
def clean_db(database: Database) -> Database:
    from app.db import Base

    with database.engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    return database


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def rt(settings: Settings, clean_db: Database, clock: FakeClock) -> Runtime:
    r = Runtime(settings, clean_db, clock)
    r.bootstrap()
    return r


def run_ticks(rt: Runtime, clock: FakeClock, n: int, step: float = 1.0) -> None:
    for _ in range(n):
        clock.advance(step)
        rt.tick()


PASSWORDS = {r: secrets.token_urlsafe(18) for r in ("admin", "operator", "approver", "viewer")}


@pytest.fixture
def api(settings, clean_db, clock):
    """TestClient with the engine loop disabled; tests drive time with `api.tick(n)`."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings.bootstrap_admin_password = PASSWORDS["admin"]
    settings.bootstrap_operator_password = PASSWORDS["operator"]
    settings.bootstrap_approver_password = PASSWORDS["approver"]
    settings.bootstrap_viewer_password = PASSWORDS["viewer"]
    app = create_app(settings, clock=clock, db=clean_db)
    with TestClient(app) as client:
        rt = app.state.runtime
        tokens: dict[str, str] = {}

        def login(user: str) -> dict[str, str]:
            if user not in tokens:
                r = client.post("/api/v1/auth/login", json={"username": user, "password": PASSWORDS[user]})
                assert r.status_code == 200, r.text
                tokens[user] = r.json()["access_token"]
            return {"Authorization": f"Bearer {tokens[user]}"}

        client.as_ = login  # type: ignore[attr-defined]
        client.rt = rt  # type: ignore[attr-defined]
        client.tick = lambda n=1: run_ticks(rt, clock, n)  # type: ignore[attr-defined]
        yield client
