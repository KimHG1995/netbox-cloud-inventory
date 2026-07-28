from datetime import timedelta

import pytest

from cloud_inventory.jobs.queue import retry_delay_for_attempt
from cloud_inventory.persistence.models import (
    ArtifactStatus,
    JobStatus,
    OwnerMapping,
    PreviewStatus,
    SourceFileStatus,
    TagMapping,
)
from cloud_inventory.persistence.repositories import (
    InvalidStateTransitionError,
    build_request_fingerprint,
    build_source_file_deduplication_key,
    normalize_mapping_key,
    require_artifact_transition,
    require_job_transition,
    require_preview_transition,
    require_source_file_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SourceFileStatus.UPLOADED, SourceFileStatus.PARSING),
        (SourceFileStatus.PARSING, SourceFileStatus.PREVIEW_READY),
        (SourceFileStatus.PREVIEW_READY, SourceFileStatus.APPLYING),
        (SourceFileStatus.APPLYING, SourceFileStatus.APPLIED),
        (SourceFileStatus.UPLOADED, SourceFileStatus.FAILED),
        (SourceFileStatus.PARSING, SourceFileStatus.FAILED),
        (SourceFileStatus.APPLYING, SourceFileStatus.FAILED),
    ],
)
def test_valid_source_file_transitions(
    current: SourceFileStatus,
    target: SourceFileStatus,
) -> None:
    require_source_file_transition(current, target)


def test_invalid_source_file_transition_is_rejected() -> None:
    with pytest.raises(InvalidStateTransitionError):
        require_source_file_transition(
            SourceFileStatus.UPLOADED,
            SourceFileStatus.APPLIED,
        )


def test_artifact_preview_and_job_transitions() -> None:
    require_artifact_transition(ArtifactStatus.AVAILABLE, ArtifactStatus.EXPIRED)
    require_preview_transition(PreviewStatus.READY, PreviewStatus.APPLYING)
    require_preview_transition(PreviewStatus.READY, PreviewStatus.EXPIRED)
    require_preview_transition(PreviewStatus.APPLYING, PreviewStatus.APPLIED)
    require_job_transition(JobStatus.QUEUED, JobStatus.RUNNING)
    require_job_transition(JobStatus.RUNNING, JobStatus.SUCCEEDED)
    require_job_transition(JobStatus.RUNNING, JobStatus.RETRY_WAIT)
    require_job_transition(JobStatus.RETRY_WAIT, JobStatus.QUEUED)
    require_job_transition(JobStatus.RUNNING, JobStatus.FAILED)

    with pytest.raises(InvalidStateTransitionError):
        require_artifact_transition(ArtifactStatus.EXPIRED, ArtifactStatus.AVAILABLE)
    with pytest.raises(InvalidStateTransitionError):
        require_preview_transition(PreviewStatus.APPLIED, PreviewStatus.READY)
    with pytest.raises(InvalidStateTransitionError):
        require_job_transition(JobStatus.QUEUED, JobStatus.SUCCEEDED)


def test_retry_policy_is_bounded() -> None:
    assert retry_delay_for_attempt(1) == timedelta(seconds=5)
    assert retry_delay_for_attempt(2) == timedelta(seconds=30)
    assert retry_delay_for_attempt(3) is None


def test_request_fingerprint_is_file_order_independent() -> None:
    forward = build_request_fingerprint(
        "aws",
        "commercial",
        "123456789012",
        ["b" * 64, "a" * 64],
    )
    reverse = build_request_fingerprint(
        "aws",
        "commercial",
        "123456789012",
        ["a" * 64, "b" * 64],
    )

    assert forward == reverse


def test_source_file_deduplication_is_account_scoped() -> None:
    first = build_source_file_deduplication_key(
        "ncp",
        "government",
        "account-01",
        "a" * 64,
    )
    second = build_source_file_deduplication_key(
        "ncp",
        "government",
        "account-02",
        "a" * 64,
    )

    assert first != second


def test_mapping_key_uses_trimmed_unicode_casefold() -> None:
    assert normalize_mapping_key("  Straße  ") == "strasse"


def test_mapping_models_normalize_before_persistence() -> None:
    tag_mapping = TagMapping(
        provider="aws",
        realm="commercial",
        account_id="123456789012",
        source_key="  Straße  ",
        source_value="  production  ",
        business_service_code="portal",
        priority=100,
        enabled=True,
    )
    owner_mapping = OwnerMapping(
        provider="aws",
        realm="commercial",
        account_id="123456789012",
        source_value="  platform-team  ",
        netbox_owner_id=10,
        priority=100,
        enabled=True,
    )

    assert tag_mapping.source_key == "  Straße  "
    assert tag_mapping.source_key_normalized == "strasse"
    assert tag_mapping.source_value == "production"
    assert owner_mapping.source_value == "platform-team"


def test_mapping_priority_is_validated_before_database_insert() -> None:
    with pytest.raises(ValueError, match="priority"):
        TagMapping(
            provider="aws",
            realm="commercial",
            account_id="123456789012",
            source_key="Environment",
            source_value="production",
            business_service_code="portal",
            priority=1001,
            enabled=True,
        )
