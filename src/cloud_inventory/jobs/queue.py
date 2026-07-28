from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cloud_inventory.persistence.models import CollectionJob, JobStatus
from cloud_inventory.persistence.repositories import require_job_transition


class PermanentJobError(Exception):
    """A job input error that cannot succeed after a retry."""


class FileValidationJobError(PermanentJobError):
    pass


class UnknownParserProfileJobError(PermanentJobError):
    pass


class SchemaValidationJobError(PermanentJobError):
    pass


class PreviewPolicyJobError(PermanentJobError):
    pass


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    job_type: str
    payload: dict[str, Any]
    attempts: int


def retry_delay_for_attempt(attempt: int) -> timedelta | None:
    return {
        1: timedelta(seconds=5),
        2: timedelta(seconds=30),
    }.get(attempt)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class JobQueue:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> UUID:
        job_id = uuid4()
        now = self._clock()
        async with self._session_factory.begin() as session:
            statement = (
                insert(CollectionJob)
                .values(
                    id=job_id,
                    job_type=job_type,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    status=JobStatus.QUEUED,
                    attempts=0,
                    available_at=now,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(CollectionJob.id)
            )
            inserted_id = (await session.execute(statement)).scalar_one_or_none()
            if inserted_id is not None:
                return inserted_id
            existing_id = (
                await session.execute(
                    select(CollectionJob.id).where(
                        CollectionJob.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one()
            return existing_id

    async def claim(self, worker_id: str) -> ClaimedJob | None:
        now = self._clock()
        async with self._session_factory.begin() as session:
            await session.execute(
                update(CollectionJob)
                .where(
                    CollectionJob.status == JobStatus.RETRY_WAIT,
                    CollectionJob.available_at <= now,
                )
                .values(status=JobStatus.QUEUED)
            )
            await session.execute(
                update(CollectionJob)
                .where(
                    CollectionJob.status == JobStatus.RUNNING,
                    CollectionJob.locked_at < now - timedelta(minutes=15),
                )
                .values(
                    status=JobStatus.QUEUED,
                    available_at=now,
                    locked_at=None,
                    locked_by=None,
                )
            )
            statement = (
                select(CollectionJob)
                .where(
                    CollectionJob.status == JobStatus.QUEUED,
                    CollectionJob.available_at <= now,
                )
                .order_by(CollectionJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                return None
            require_job_transition(job.status, JobStatus.RUNNING)
            job.status = JobStatus.RUNNING
            job.locked_by = worker_id
            job.locked_at = now
            job.attempts += 1
            await session.flush()
            return ClaimedJob(
                id=job.id,
                job_type=job.job_type,
                payload=job.payload,
                attempts=job.attempts,
            )

    async def succeed(self, job_id: UUID, result: dict[str, Any]) -> None:
        async with self._session_factory.begin() as session:
            job = (
                await session.execute(
                    select(CollectionJob)
                    .where(CollectionJob.id == job_id)
                    .with_for_update()
                )
            ).scalar_one()
            require_job_transition(job.status, JobStatus.SUCCEEDED)
            job.status = JobStatus.SUCCEEDED
            job.result = result
            job.locked_at = None
            job.locked_by = None

    async def fail(self, job_id: UUID, error: Exception) -> JobStatus:
        now = self._clock()
        async with self._session_factory.begin() as session:
            job = (
                await session.execute(
                    select(CollectionJob)
                    .where(CollectionJob.id == job_id)
                    .with_for_update()
                )
            ).scalar_one()
            job.last_error = str(error)
            delay = retry_delay_for_attempt(job.attempts)
            if isinstance(error, PermanentJobError) or delay is None:
                require_job_transition(job.status, JobStatus.FAILED)
                job.status = JobStatus.FAILED
                job.locked_at = None
                job.locked_by = None
                return JobStatus.FAILED

            require_job_transition(job.status, JobStatus.RETRY_WAIT)
            job.status = JobStatus.RETRY_WAIT
            job.available_at = now + delay
            job.locked_at = None
            job.locked_by = None
            return JobStatus.RETRY_WAIT
