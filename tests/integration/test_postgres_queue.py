import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

from cloud_inventory.api.imports import ImportRecord, SourceFileRecord
from cloud_inventory.api.mappings import MappingConflictError
from cloud_inventory.domain.models import (
    CloudResource,
    Completeness,
    DetailLevel,
    Provider,
    Realm,
    ResourceType,
)
from cloud_inventory.ingest.batch import finalize_batch
from cloud_inventory.jobs.queue import (
    FileValidationJobError,
    JobQueue,
    PreviewPolicyJobError,
    SchemaValidationJobError,
    UnknownParserProfileJobError,
)
from cloud_inventory.netbox.writer import AppliedChange, ApplyResult
from cloud_inventory.persistence.base import Base
from cloud_inventory.persistence.models import (
    ChangeSummary,
    CollectionJob,
    JobStatus,
    SourceFile,
    SourceFileStatus,
)
from cloud_inventory.persistence.repositories import ImportRepository
from cloud_inventory.reconciliation.diff import Reconciler


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


@pytest.mark.asyncio
async def test_import_and_apply_jobs_are_created_atomically_and_idempotently(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = ImportRepository(session_factory)
    now = datetime(2026, 7, 28, 3, 0, tzinfo=UTC)
    import_id = uuid4()
    record = ImportRecord(
        id=import_id,
        provider=Provider.AWS,
        realm=Realm.COMMERCIAL,
        account_id="111111111111",
        export_type="auto",
        region="ap-northeast-2",
        exported_at=now,
        request_fingerprint="a" * 64,
        created_at=now,
        created_by="test",
    )
    source = SourceFileRecord(
        id=uuid4(),
        import_id=import_id,
        filename="resources.csv",
        media_type="text/csv",
        sha256="b" * 64,
        deduplication_key="c" * 64,
        size_bytes=10,
        artifact_key=f"imports/{import_id}/{uuid4()}/{'b' * 64}",
        expires_at=now + timedelta(days=30),
    )

    await repository.create_import(record, [source])
    duplicate = await repository.find_import_by_fingerprint("a" * 64)

    assert duplicate is not None
    assert duplicate.id == import_id
    assert duplicate.parse_job_id == record.parse_job_id

    run, created = await repository.create_or_get_run(
        import_id=import_id,
        batch_hash="d" * 64,
        apply_valid_only=False,
        idempotency_key=f"apply:{import_id}:{'d' * 64}:false",
    )
    repeated, repeated_created = await repository.create_or_get_run(
        import_id=import_id,
        batch_hash="d" * 64,
        apply_valid_only=False,
        idempotency_key=f"apply:{import_id}:{'d' * 64}:false",
    )

    assert created is True
    assert repeated_created is False
    assert repeated.id == run.id


@pytest.mark.asyncio
async def test_mapping_upsert_enforces_one_active_source_selector(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = ImportRepository(session_factory)
    values = {
        "provider": "aws",
        "realm": "commercial",
        "account_id": "111111111111",
        "source_key": "Service",
        "source_value": "payments",
        "business_service_code": "payments",
        "priority": 100,
        "enabled": True,
    }
    mapping_id = uuid4()

    created, was_created = await repository.upsert_tag_mapping(mapping_id, values)
    updated, was_created_again = await repository.upsert_tag_mapping(
        mapping_id,
        {**values, "priority": 200},
    )

    assert was_created is True
    assert was_created_again is False
    assert created.id == updated.id
    assert updated.values["priority"] == 200
    with pytest.raises(MappingConflictError):
        await repository.upsert_tag_mapping(uuid4(), values)


@pytest.mark.asyncio
async def test_worker_state_persists_preview_checkpoint_summary_and_expiry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = ImportRepository(session_factory)
    now = datetime(2026, 7, 28, 5, 0, tzinfo=UTC)
    import_id = uuid4()
    source_id = uuid4()
    record = ImportRecord(
        id=import_id,
        provider=Provider.AWS,
        realm=Realm.COMMERCIAL,
        account_id="111111111111",
        export_type="auto",
        region="ap-northeast-2",
        exported_at=now,
        request_fingerprint="1" * 64,
        created_at=now,
        created_by="test",
    )
    source = SourceFileRecord(
        id=source_id,
        import_id=import_id,
        filename="resources.csv",
        media_type="text/csv",
        sha256="2" * 64,
        deduplication_key="3" * 64,
        size_bytes=10,
        artifact_key=f"imports/{import_id}/{source_id}/{'2' * 64}",
        expires_at=now,
    )
    await repository.create_import(record, [source])
    await repository.mark_source_parsing(source_id)
    await repository.set_source_parser(source_id, "fake.v1", "1")
    resource = CloudResource(
        uid=(
            "aws:commercial:111111111111:ap-northeast-2:"
            "virtual_machine:i-123"
        ),
        provider=Provider.AWS,
        realm=Realm.COMMERCIAL,
        account_id="111111111111",
        region="ap-northeast-2",
        resource_type=ResourceType.VIRTUAL_MACHINE,
        external_id="i-123",
        name="app-01",
        status="active",
        observed_at=now,
        completeness=Completeness.PARTIAL,
        detail_level=DetailLevel.SUMMARY,
        source_profile="fake.v1",
        source_priority=100,
    )
    batch = finalize_batch(
        provider=Provider.AWS,
        realm=Realm.COMMERCIAL,
        account_id="111111111111",
        observed_at=now,
        resources=[resource],
        parser_profile="fake.v1",
        source_priority=100,
        detail_level=DetailLevel.SUMMARY,
    )
    preview = Reconciler().preview(batch, {})
    await repository.save_preview(
        import_id,
        preview,
        {"fake.v1": "1"},
        now + timedelta(hours=24),
    )
    await repository.mark_sources_preview_ready(import_id)
    loaded = await repository.load_preview(import_id)
    assert loaded is not None
    assert loaded[0].batch_hash == preview.batch_hash

    run, _ = await repository.create_or_get_run(
        import_id=import_id,
        batch_hash=preview.batch_hash,
        apply_valid_only=False,
        idempotency_key=f"apply:{import_id}:{preview.batch_hash}:false",
    )
    await repository.mark_run_running(run.id)
    await repository.set_run_checkpoint(run.id, 3)
    await repository.finish_run(
        run.id,
        ApplyResult(
            created=1,
            updated=0,
            unchanged=0,
            warnings=0,
            failed=0,
            checkpoint=9,
            changes=[
                AppliedChange(
                    cloud_uid=resource.uid,
                    action="create",
                    changed_fields=["name"],
                    warning_codes=[],
                    netbox_object_id=10,
                )
            ],
        ),
    )
    await repository.mark_import_applied(import_id)
    expired = await repository.list_expired_artifacts(now)
    assert [item.id for item in expired] == [source_id]
    await repository.mark_artifact_expired(source_id)

    async with session_factory() as session:
        persisted_source = await session.get(SourceFile, source_id)
        summaries = (
            await session.execute(
                select(ChangeSummary).where(ChangeSummary.run_id == run.id)
            )
        ).scalars().all()
    assert persisted_source is not None
    assert persisted_source.status is SourceFileStatus.APPLIED
    assert persisted_source.parser_version == "1"
    assert len(summaries) == 1
