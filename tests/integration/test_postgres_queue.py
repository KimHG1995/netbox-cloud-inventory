import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

from cloud_inventory.jobs.queue import (
    FileValidationJobError,
    JobQueue,
    PreviewPolicyJobError,
    SchemaValidationJobError,
    UnknownParserProfileJobError,
)
from cloud_inventory.persistence.base import Base
from cloud_inventory.persistence.models import CollectionJob, JobStatus


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 28, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture(scope="module")
def postgres_database_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as postgres:
        sync_url = postgres.get_connection_url(driver=None)
        database_url = sync_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )
        alembic_config = Config("alembic.ini")
        alembic_config.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(alembic_config, "head")
        yield database_url
        command.downgrade(alembic_config, "base")


@pytest_asyncio.fixture
async def session_factory(
    postgres_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(postgres_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            await connection.execute(delete(table))
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_workers_claim_one_job_atomically(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = MutableClock()
    queue = JobQueue(session_factory, clock=clock)
    job_id = await queue.enqueue("parse_import", {"import_id": "one"}, "job-one")

    first, second = await asyncio.gather(
        queue.claim("worker-a"),
        queue.claim("worker-b"),
    )

    claimed = [job for job in (first, second) if job is not None]
    assert len(claimed) == 1
    assert claimed[0].id == job_id
    assert claimed[0].attempts == 1
    await queue.succeed(job_id, {"status": "ok"})


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_and_transient_retries_are_bounded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = MutableClock()
    queue = JobQueue(session_factory, clock=clock)
    original = await queue.enqueue("parse_import", {}, "retry-job")
    duplicate = await queue.enqueue("parse_import", {"ignored": True}, "retry-job")
    assert duplicate == original

    first = await queue.claim("worker")
    assert first is not None
    assert await queue.fail(first.id, RuntimeError("temporary")) is JobStatus.RETRY_WAIT
    assert await queue.claim("worker") is None

    clock.advance(timedelta(seconds=5))
    second = await queue.claim("worker")
    assert second is not None
    assert second.attempts == 2
    assert await queue.fail(second.id, RuntimeError("temporary")) is JobStatus.RETRY_WAIT

    clock.advance(timedelta(seconds=30))
    third = await queue.claim("worker")
    assert third is not None
    assert third.attempts == 3
    assert await queue.fail(third.id, RuntimeError("temporary")) is JobStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        FileValidationJobError("invalid file"),
        UnknownParserProfileJobError("unknown parser"),
        SchemaValidationJobError("invalid schema"),
        PreviewPolicyJobError("invalid preview"),
    ],
)
async def test_permanent_errors_fail_on_first_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    error: Exception,
) -> None:
    queue = JobQueue(session_factory, clock=MutableClock())
    job_id = await queue.enqueue("parse_import", {}, error.__class__.__name__)
    claimed = await queue.claim("worker")
    assert claimed is not None

    assert await queue.fail(job_id, error) is JobStatus.FAILED


@pytest.mark.asyncio
async def test_stale_lock_is_recovered(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = MutableClock()
    queue = JobQueue(session_factory, clock=clock)
    job_id = await queue.enqueue("parse_import", {}, "stale-job")
    claimed = await queue.claim("dead-worker")
    assert claimed is not None

    async with session_factory.begin() as session:
        await session.execute(
            update(CollectionJob)
            .where(CollectionJob.id == job_id)
            .values(locked_at=clock() - timedelta(minutes=16))
        )

    recovered = await queue.claim("replacement-worker")

    assert recovered is not None
    assert recovered.id == job_id
    assert recovered.attempts == 2
