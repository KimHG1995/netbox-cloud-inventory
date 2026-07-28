# Manual Export Import PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AWS와 NAVER Cloud Platform의 수동 Export 파일을 업로드하고 변경 내용을 승인한 뒤 NetBox에서 계정, 네트워크, 서버, IP, Load Balancer, Database, Bucket, DNS와 담당 관계를 조회할 수 있는 최소 PoC를 만든다.

**Architecture:** FastAPI가 파일 업로드와 미리보기 승인 API를 제공하고 PostgreSQL 작업 큐가 별도 Worker에 Parsing과 NetBox 반영 작업을 전달한다. 모든 입력은 `ResourceBatch`로 정규화하며, PoC에서는 API와 Worker가 공유하는 전용 로컬 볼륨에 원본 파일을 저장하고 NetBox 4.6.5와 Custom Objects 0.6.x에는 정규화 자산만 저장한다. Artifact Store 인터페이스는 운영 환경의 S3 호환 저장소 구현을 나중에 추가할 수 있도록 분리한다.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, PostgreSQL 16, Alembic, HTTPX, openpyxl, Jinja2, pytest, Ruff, mypy, Docker Compose, NetBox 4.6.5, NetBox Custom Objects 0.6.x

## Global Constraints

- 이 계획에서는 AWS와 NAVER Cloud Platform API를 호출하지 않는다.
- 자동 API Collector는 수동 Import PoC가 검증된 뒤 별도 계획으로 구현한다.
- AWS Resource Explorer CSV를 공급자 요약 입력으로 지원한다.
- NAVER Cloud Platform Server, Public IP, Load Balancer, Object Storage Bucket 목록 XLSX를 공급자 입력으로 지원한다.
- VPC, Subnet, NIC, Database, DNS와 상세 관계는 표준 JSON Import Bundle로 입력할 수 있다.
- 공급자 파일에 없는 필드와 관계는 추정하지 않는다.
- 수동 파일의 누락을 리소스 삭제나 `inactive` 전환 근거로 사용하지 않는다.
- 파일 하나의 최대 크기는 100 MB이고 한 Import에는 최대 20개 파일을 허용한다.
- 동일 파일과 동일 Batch의 반복 처리는 중복 객체를 만들지 않아야 한다.
- NetBox Core의 사용자 관리 필드, Owner, 설명, 운영 메모를 덮어쓰지 않는다.
- BusinessService와 Owner는 운영자가 NetBox에서 먼저 만들며 Import가 자동 생성하지 않는다.
- 수집 서비스의 작업 큐는 PostgreSQL `FOR UPDATE SKIP LOCKED`를 사용하며 별도 Redis를 사용하지 않는다.
- NetBox 배포의 Redis는 NetBox와 Custom Objects 플러그인 전용이다.
- PoC 원본 파일은 API와 Worker만 접근하는 Docker named volume에 저장하며 Host Port로 노출하지 않는다.
- 운영용 S3 호환 Artifact Store Adapter는 자동 API Collector와 함께 후속 계획에서 구현한다.
- 공개 저장소에는 실제 Export 파일, 내부 계정 ID, 내부 Domain, 실제 IP, 자격증명을 커밋하지 않는다.
- 모든 Fixture는 합성 데이터만 사용한다.
- PoC의 HTTP Port는 `127.0.0.1`에만 Bind하고 사내 SSO가 적용된 운영 서비스로 간주하지 않는다.
- PoC writes `created_by="local-poc"` because it has no user authentication; non-local deployment is blocked until authentication and real actor identity are added.
- 문서와 사용자 화면 문구에 가운뎃점을 사용하지 않는다.
- 라이선스는 Apache License 2.0을 유지한다.

---

## File Map

```text
.env.example
.dockerignore
.gitignore
.github/workflows/ci.yml
.python-version
alembic.ini
compose.yaml
pyproject.toml
uv.lock

deploy/netbox/Dockerfile
deploy/netbox/plugin_requirements.txt
deploy/netbox/configuration/plugins.py
deploy/netbox/configuration/extra.py
deploy/inventory/Dockerfile

schemas/import-bundle-v1.schema.json
schemas/netbox/custom-objects-v1.json

scripts/apply_netbox_schema.py
scripts/export_import_schema.py
scripts/poc_import.sh

src/cloud_inventory/__init__.py
src/cloud_inventory/app.py
src/cloud_inventory/config.py

src/cloud_inventory/api/dependencies.py
src/cloud_inventory/api/imports.py
src/cloud_inventory/api/mappings.py
src/cloud_inventory/api/runs.py
src/cloud_inventory/api/ui.py
src/cloud_inventory/api/templates/imports.html
src/cloud_inventory/api/templates/preview.html

src/cloud_inventory/domain/models.py
src/cloud_inventory/domain/uid.py

src/cloud_inventory/ingest/artifact_store.py
src/cloud_inventory/ingest/batch.py
src/cloud_inventory/ingest/file_validation.py
src/cloud_inventory/ingest/parsers/base.py
src/cloud_inventory/ingest/parsers/registry.py
src/cloud_inventory/ingest/parsers/import_bundle.py
src/cloud_inventory/ingest/parsers/aws_resource_explorer.py
src/cloud_inventory/ingest/parsers/ncp_console.py

src/cloud_inventory/persistence/base.py
src/cloud_inventory/persistence/models.py
src/cloud_inventory/persistence/session.py
src/cloud_inventory/persistence/repositories.py
src/cloud_inventory/persistence/migrations/env.py
src/cloud_inventory/persistence/migrations/versions/0001_control_tables.py

src/cloud_inventory/jobs/queue.py
src/cloud_inventory/jobs/worker.py

src/cloud_inventory/reconciliation/diff.py
src/cloud_inventory/reconciliation/fingerprint.py

src/cloud_inventory/netbox/client.py
src/cloud_inventory/netbox/bootstrap.py
src/cloud_inventory/netbox/writer.py

tests/conftest.py
tests/api/test_health.py
tests/unit/domain/test_uid.py
tests/unit/domain/test_models.py
tests/unit/ingest/test_batch.py
tests/unit/ingest/test_artifact_store.py
tests/unit/ingest/test_file_validation.py
tests/unit/ingest/test_import_bundle.py
tests/unit/ingest/test_aws_resource_explorer.py
tests/unit/ingest/test_ncp_console.py
tests/unit/jobs/test_queue.py
tests/unit/reconciliation/test_diff.py
tests/unit/netbox/test_client.py
tests/unit/netbox/test_writer.py
tests/api/test_imports.py
tests/api/test_mappings.py
tests/api/test_ui.py
tests/integration/test_postgres_queue.py
tests/integration/test_netbox_schema.py
tests/integration/test_manual_import_flow.py
tests/fixtures/aws/resource-explorer.csv
tests/fixtures/import-bundle/full-inventory.json
```

각 모듈의 책임은 다음과 같다.

- `domain`: 공급자와 저장소에 독립적인 정규화 모델과 식별자 생성
- `ingest`: 원본 검증, Artifact 저장, 공급자 파일 Parsing
- `persistence`: Control Database 모델과 Repository
- `jobs`: PostgreSQL 작업 선점과 Worker 실행
- `reconciliation`: 현재 NetBox 상태와 Batch의 차이 계산
- `netbox`: NetBox Schema 초기화, REST API 호출, 의존성 순서 Upsert
- `api`: Import 생성, 미리보기, 승인, 실행 상태, 최소 HTML 화면

---

### Task 1: Python Project and Health Endpoint

**Files:**

- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.env.example`
- Create: `.dockerignore`
- Create: `.gitignore`
- Create: `src/cloud_inventory/__init__.py`
- Create: `src/cloud_inventory/config.py`
- Create: `src/cloud_inventory/app.py`
- Create: `tests/conftest.py`
- Create: `tests/api/test_health.py`

**Interfaces:**

- Produces: `cloud_inventory.app:create_app() -> FastAPI`
- Produces: `cloud_inventory.config:Settings`
- Produces: `GET /healthz -> {"status": "ok"}`

- [ ] **Step 1: Write the failing health test**

```python
from fastapi.testclient import TestClient

from cloud_inventory.app import create_app


def test_healthz_returns_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the test and verify the package is absent**

Run: `uv run pytest tests/api/test_health.py -q`

Expected: FAIL because `cloud_inventory.app` does not exist.

- [ ] **Step 3: Add the project metadata and dependencies**

Create `pyproject.toml` with Python `>=3.12,<3.13`, a `src` package layout, and these runtime dependencies:

```toml
dependencies = [
  "alembic>=1.16,<2",
  "fastapi>=0.116,<1",
  "httpx>=0.28,<1",
  "itsdangerous>=2.2,<3",
  "jinja2>=3.1,<4",
  "openpyxl>=3.1,<4",
  "psycopg[binary,pool]>=3.2,<4",
  "pydantic>=2.11,<3",
  "pydantic-settings>=2.10,<3",
  "python-multipart>=0.0.20,<1",
  "sqlalchemy[asyncio]>=2.0.41,<3",
  "uvicorn[standard]>=0.35,<1",
]
```

Add development dependencies for `mypy`, `pytest`, `pytest-asyncio`, `respx`, `ruff`, and `testcontainers`.

- [ ] **Step 4: Implement settings and the application factory**

```python
from functools import lru_cache

from pathlib import Path

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVENTORY_", env_file=".env")

    database_url: str
    artifact_root: Path = Path("/var/lib/cloud-inventory/artifacts")
    netbox_url: AnyHttpUrl = "http://localhost:8000"
    netbox_token: SecretStr
    csrf_secret: SecretStr
    max_file_bytes: int = 100 * 1024 * 1024
    max_files_per_import: int = 20
    artifact_retention_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`create_app()`는 FastAPI lifespan을 사용하고 `/healthz`를 등록한다. Module import 시에는 외부 연결을 생성하지 않는다.

`.env.example` lists every secret variable with an empty value. `tests/conftest.py` sets deterministic test-only values through `monkeypatch`; production code has no credential defaults.

`.gitignore` must include `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.pyc`, `artifacts/`, and `private-fixtures/`.

`.dockerignore` must include `.env`, `.git`, `.venv`, Python and test caches, `artifacts`, and real export extensions below `private-fixtures/`. The Docker build must not receive `.env`.

- [ ] **Step 5: Lock dependencies and run quality checks**

Run:

```bash
uv lock
uv run pytest tests/api/test_health.py -q
uv run ruff check src tests
uv run mypy src
```

Expected: 모든 명령 성공.

- [ ] **Step 6: Commit the scaffold**

```bash
git add pyproject.toml uv.lock .python-version .env.example .dockerignore .gitignore src tests/api/test_health.py tests/conftest.py
git commit -m "build: scaffold manual import service"
```

---

### Task 2: Canonical Resource Models and Stable Identifiers

**Files:**

- Create: `src/cloud_inventory/domain/models.py`
- Create: `src/cloud_inventory/domain/uid.py`
- Create: `scripts/export_import_schema.py`
- Create: `schemas/import-bundle-v1.schema.json`
- Create: `tests/unit/domain/test_uid.py`
- Create: `tests/unit/domain/test_models.py`

**Interfaces:**

- Produces: `build_cloud_uid(provider, realm, account_id, region, resource_type, external_id) -> str`
- Produces: `ImportResource`, `CloudResource`, `Relationship`, `ResourceBatch`, `ImportBundle`
- Consumed by: every Parser, Reconciler, NetBox Writer

- [ ] **Step 1: Write failing identifier and model tests**

```python
def test_cloud_uid_is_stable_and_escapes_colons() -> None:
    assert build_cloud_uid(
        provider=Provider.NCP,
        realm=Realm.GOVERNMENT,
        account_id="account:01",
        region="KR",
        resource_type=ResourceType.VIRTUAL_MACHINE,
        external_id="server/123",
    ) == "ncp:government:account%3A01:KR:virtual_machine:server%2F123"


def test_export_resource_cannot_claim_full_completeness() -> None:
    with pytest.raises(ValidationError):
        CloudResource(
            uid="x",
            provider="aws",
            realm="commercial",
            account_id="123456789012",
            region="ap-northeast-2",
            resource_type="virtual_machine",
            external_id="i-1",
            name="vm-1",
            source="export",
            completeness="full",
            detail_level="summary",
            source_profile="test.export.v1",
            source_priority=1,
            observed_at="2026-07-28T00:00:00Z",
        )
```

Add tests that `attributes` or `tags` containing a normalized key equal to `password`, `passwd`, `secret`, `token`, `accesskey`, `secretkey`, `privatekey`, `credential`, or `connectionstring` are rejected.

- [ ] **Step 2: Run the focused tests**

Run: `uv run pytest tests/unit/domain -q`

Expected: FAIL because the domain package is absent.

- [ ] **Step 3: Implement exact enums and models**

Define:

```python
class Provider(StrEnum):
    AWS = "aws"
    NCP = "ncp"


class Realm(StrEnum):
    COMMERCIAL = "commercial"
    GOVERNMENT = "government"


class ResourceType(StrEnum):
    CLOUD_ACCOUNT = "cloud_account"
    REGION = "region"
    ZONE = "zone"
    VPC = "vpc"
    SUBNET = "subnet"
    VIRTUAL_MACHINE = "virtual_machine"
    NETWORK_INTERFACE = "network_interface"
    IP_ADDRESS = "ip_address"
    LOAD_BALANCER = "load_balancer"
    DOMAIN = "domain"
    DNS_ZONE = "dns_zone"
    DNS_RECORD = "dns_record"
    MANAGED_DATABASE = "managed_database"
    OBJECT_BUCKET = "object_bucket"


class Completeness(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class DetailLevel(StrEnum):
    SUMMARY = "summary"
    DETAILED = "detailed"


class Relationship(BaseModel):
    relation_type: Literal[
        "contains", "attached_to", "assigned_to", "resolves_to",
        "routes_to", "serves"
    ]
    target_uid: str


class ResourceDocument(BaseModel):
    schema_version: Literal["1"] = "1"
    uid: str
    provider: Provider
    realm: Realm
    account_id: str
    region: str
    resource_type: ResourceType
    external_id: str
    name: str
    status: str = "unknown"
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    relationships: list[Relationship] = Field(default_factory=list)
    observed_at: AwareDatetime
    warnings: list[str] = Field(default_factory=list)


class ImportResource(ResourceDocument):
    pass


class CloudResource(ResourceDocument):
    source: Literal["export"] = "export"
    completeness: Completeness
    detail_level: DetailLevel
    source_profile: str
    source_priority: int = Field(ge=0, le=1000)


class ResourceScope(BaseModel):
    region: str
    resource_type: ResourceType
    completeness: Completeness


class ResourceBatch(BaseModel):
    schema_version: Literal["1"] = "1"
    batch_id: UUID
    provider: Provider
    realm: Realm
    account_id: str
    observed_at: AwareDatetime
    completeness: Completeness
    scopes: list[ResourceScope]
    resources: list[CloudResource]
    parser_profiles: list[str]
    warnings: list[str] = Field(default_factory=list)
    content_hash: str


class ImportBundle(BaseModel):
    schema_version: Literal["1"]
    provider: Provider
    realm: Realm
    account_id: str
    exported_at: AwareDatetime
    resources: list[ImportResource]
```

`ResourceDocument`, `ImportResource`, `CloudResource`, `ResourceBatch`, and `ImportBundle` all use `ConfigDict(extra="forbid")`. This prevents a Bundle author from supplying the internal `source`, `completeness`, `detail_level`, `source_profile`, or `source_priority` fields.

Reject `source="export"` resources with `completeness="full"`. Every manual Parser emits a partial Batch and partial Scopes. `detail_level` describes field richness independently: provider list exports use `summary`, while the standard Import Bundle uses `detailed`.

Normalize an attribute or tag key for sensitivity checks with `"".join(character for character in key.casefold() if character.isalnum())`. Reject a ResourceDocument when any key at any nested attributes level, or any tag key, equals one of the sensitive names from Step 1. Do not inspect ordinary values with heuristics.

Validate that all resources match the Bundle provider, realm, and account and that every relationship target is either present in the Bundle or recorded as `unresolved_relation`.

- [ ] **Step 4: Implement canonical identifier encoding**

Use `urllib.parse.quote(value, safe="-._~")` for every component and join exactly six components with `:`.

- [ ] **Step 5: Generate and verify the JSON Schema**

`scripts/export_import_schema.py` must write:

```python
schema = ImportBundle.model_json_schema(mode="validation")
target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
```

Add a test that generates the schema in memory and compares it with `schemas/import-bundle-v1.schema.json`.

- [ ] **Step 6: Run domain tests and commit**

```bash
uv run pytest tests/unit/domain -q
uv run python scripts/export_import_schema.py --check
git add src/cloud_inventory/domain scripts/export_import_schema.py schemas/import-bundle-v1.schema.json tests/unit/domain
git commit -m "feat: define canonical cloud resource model"
```

---

### Task 3: Parser Contract and Standard Import Bundle

**Files:**

- Create: `src/cloud_inventory/ingest/parsers/base.py`
- Create: `src/cloud_inventory/ingest/parsers/registry.py`
- Create: `src/cloud_inventory/ingest/parsers/import_bundle.py`
- Create: `src/cloud_inventory/ingest/batch.py`
- Create: `tests/unit/ingest/test_import_bundle.py`
- Create: `tests/unit/ingest/test_batch.py`
- Create: `tests/fixtures/import-bundle/full-inventory.json`

**Interfaces:**

- Produces: `SourceMetadata`
- Produces: `Parser.parse(path: Path, metadata: SourceMetadata) -> ResourceBatch`
- Produces: `ParserRegistry.detect(path, metadata) -> Parser`
- Produces: `ImportBundleParser`
- Produces: `finalize_batch(...) -> ResourceBatch`
- Produces: `combine_batches(batches: Sequence[ResourceBatch]) -> ResourceBatch`
- Consumes: domain models from Task 2

- [ ] **Step 1: Write failing Parser Registry tests**

```python
def test_registry_selects_import_bundle_by_content(tmp_path: Path) -> None:
    source = tmp_path / "renamed.data"
    source.write_text('{"schema_version":"1","provider":"aws","realm":"commercial",'
                      '"account_id":"123456789012","exported_at":"2026-07-28T00:00:00Z",'
                      '"resources":[]}')

    parser = build_default_registry().detect(source, aws_metadata())

    assert parser.profile_id == "canonical.import_bundle.v1"
```

Add tests for provider mismatch, duplicate `uid`, unresolved relationship warnings, and a timestamp more than five minutes in the future.

- [ ] **Step 2: Run the parser tests**

Run: `uv run pytest tests/unit/ingest/test_import_bundle.py -q`

Expected: FAIL because Parser classes do not exist.

- [ ] **Step 3: Define the Parser contract**

```python
@dataclass(frozen=True)
class SourceMetadata:
    provider: Provider
    realm: Realm
    account_id: str
    export_type: str
    uploaded_at: datetime
    exported_at: datetime
    region: str | None


class Parser(Protocol):
    profile_id: str
    schema_version: str

    def detect(self, path: Path, metadata: SourceMetadata) -> DetectionResult: ...
    def parse(self, path: Path, metadata: SourceMetadata) -> ResourceBatch: ...
```

`DetectionResult`는 `matched: bool`, `confidence: int`, `reason: str`를 가진다. Registry는 가장 높은 confidence 하나를 선택하고 동률이면 `AmbiguousParserError`를 발생시킨다. `metadata.export_type="auto"`이면 모든 Parser를 검사한다. 다른 값이면 같은 `profile_id`를 가진 Parser만 검사하고 콘텐츠가 일치하지 않으면 `ParserProfileMismatchError`를 발생시킨다.

Validate `uploaded_at` and `exported_at` as timezone-aware values at the ingestion boundary and normalize them to UTC.

- [ ] **Step 4: Implement Import Bundle parsing**

The Parser must:

- Read UTF-8 JSON only.
- Validate with `ImportBundle.model_validate_json`.
- Reject provider, realm, or account mismatch.
- Reject a Bundle `exported_at` that does not equal upload metadata after conversion to UTC.
- Reject future `exported_at` beyond five minutes.
- Recompute every `cloud_uid` and reject a mismatch.
- Convert every `ImportResource` into `CloudResource` with `source_profile="canonical.import_bundle.v1"` and `source_priority=300`.
- Force `completeness="partial"` and `detail_level="detailed"` for every converted resource.
- Build one partial ResourceScope for every distinct `(region, resource_type)` pair.
- Sort resources by `uid`.
- Compute `content_hash` as SHA-256 of canonical JSON containing `schema_version`, `provider`, `realm`, `account_id`, `observed_at`, `completeness`, sorted Scopes, sorted resources, parser profiles, and warnings. Use sorted keys, compact separators, UTC timestamps ending in `Z`, and exclude `batch_id` and `content_hash` to avoid a circular value.
- Set `batch_id` to `uuid5(NAMESPACE_URL, f"https://github.com/KimHG1995/netbox-cloud-inventory/batches/{content_hash}")`.
- Keep unresolved relationships and append a warning without inventing targets.

Put canonical sorting, Scope generation, content hashing, and UUIDv5 generation in `ingest/batch.py:finalize_batch` and require every Parser to use it.

`finalize_batch` ensures exactly one `cloud_account` resource exists. When the input does not provide it, create it only from trusted upload metadata with `external_id=account_id`, `region="global"`, name `{provider}:{realm}:{account_id}`, status `unknown`, and the current Parser profile and priority. Add sorted `contains` relations from that account to every Region resource in the Batch. This is metadata materialization, not discovery of an unreported cloud resource.

- [ ] **Step 5: Add a complete synthetic Bundle fixture**

The fixture must contain one synthetic resource for each first-scope type and relations:

```text
cloud_account -> region -> zone
cloud_account -> vpc -> subnet
subnet -> virtual_machine -> network_interface -> ip_address
load_balancer -> virtual_machine
dns_record -> load_balancer
domain -> dns_zone
managed_database -> subnet
object_bucket -> service_hint in attributes
object_bucket -> owner_hint in attributes
```

Use documentation ranges such as `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`, account `123456789012`, and domains below `example.test`.

- [ ] **Step 6: Implement deterministic multi-file Batch merging**

`combine_batches` must require identical provider, realm, and account ID. For each duplicate `uid`, rank candidate resources by `(observed_at, source_priority, source_profile, canonical_resource_sha256)` descending. Merge using:

```text
newer observed_at wins
same observed_at uses higher source_priority
canonical.import_bundle.v1 priority 300
NCP service XLSX profiles priority 200
AWS Resource Explorer priority 100
same timestamp and priority use source_profile, then canonical resource SHA-256 as a deterministic tie-break
empty strings, nulls, empty lists, empty objects, and unknown never erase a non-empty value
attributes merge one top-level key at a time using candidate rank
tags merge by exact original key using candidate rank
relationships are a sorted set of relation_type and target_uid
```

Set the combined `observed_at` to the latest child value and the combined completeness to `partial`. Union Scopes by `(region, resource_type)` and keep them partial, sort and deduplicate `parser_profiles`, compute the combined content hash from the sorted child Batch hashes, and derive `batch_id` with the same UUIDv5 rule. Add `tests/unit/ingest/test_batch.py` for reversed file order, equal-priority conflict, and empty-value preservation.

- [ ] **Step 7: Run and commit**

```bash
uv run pytest tests/unit/ingest/test_import_bundle.py tests/unit/ingest/test_batch.py -q
git add src/cloud_inventory/ingest tests/unit/ingest/test_import_bundle.py tests/unit/ingest/test_batch.py tests/fixtures/import-bundle
git commit -m "feat: parse canonical inventory bundles"
```

---

### Task 4: AWS Resource Explorer CSV Parser

**Files:**

- Create: `src/cloud_inventory/ingest/parsers/aws_resource_explorer.py`
- Modify: `src/cloud_inventory/ingest/parsers/registry.py`
- Create: `tests/unit/ingest/test_aws_resource_explorer.py`
- Create: `tests/fixtures/aws/resource-explorer.csv`

**Interfaces:**

- Produces: `AwsResourceExplorerCsvParser`
- Consumes: `Parser`, `SourceMetadata`, `CloudResource`, `build_cloud_uid`
- Header contract: `Identifier`, `Resource type`, `Region`, `AWS account`, optional `Total tags`, and zero or more tag-key columns

- [ ] **Step 1: Write failing AWS CSV tests**

Cover:

- Header detection independent of column order and case.
- UTF-8 BOM.
- AWS account mismatch rejection.
- Tag columns projected into `tags`.
- Global S3 Bucket with empty Region normalized to `global`.
- Unknown Resource Type skipped with a warning.
- Duplicate rows collapsed by `cloud_uid`.
- One Region resource emitted for every distinct non-empty, non-global Region value.

Example assertion:

```python
def test_parses_ec2_instance_and_name_tag() -> None:
    batch = parser.parse(fixture("aws/resource-explorer.csv"), aws_metadata())
    vm = next(r for r in batch.resources if r.resource_type == ResourceType.VIRTUAL_MACHINE)

    assert vm.external_id == "i-0123456789abcdef0"
    assert vm.name == "poc-web-01"
    assert vm.tags == {"Name": "poc-web-01", "Environment": "test"}
    assert vm.completeness == Completeness.PARTIAL
    assert vm.detail_level == DetailLevel.SUMMARY
```

- [ ] **Step 2: Run the AWS tests**

Run: `uv run pytest tests/unit/ingest/test_aws_resource_explorer.py -q`

Expected: FAIL because the AWS Parser is absent.

- [ ] **Step 3: Implement supported AWS type mapping**

Use this exact map:

```python
AWS_TYPE_MAP = {
    "ec2:instance": ResourceType.VIRTUAL_MACHINE,
    "ec2:networkinterface": ResourceType.NETWORK_INTERFACE,
    "ec2:vpc": ResourceType.VPC,
    "ec2:subnet": ResourceType.SUBNET,
    "ec2:elasticip": ResourceType.IP_ADDRESS,
    "elasticloadbalancing:loadbalancer": ResourceType.LOAD_BALANCER,
    "elasticloadbalancingv2:loadbalancer": ResourceType.LOAD_BALANCER,
    "route53:hostedzone": ResourceType.DNS_ZONE,
    "route53domains:domain": ResourceType.DOMAIN,
    "rds:db": ResourceType.MANAGED_DATABASE,
    "rds:dbinstance": ResourceType.MANAGED_DATABASE,
    "rds:cluster": ResourceType.MANAGED_DATABASE,
    "rds:dbcluster": ResourceType.MANAGED_DATABASE,
    "s3:bucket": ResourceType.OBJECT_BUCKET,
}
```

Normalize the resource type by accepting both `service:type` and `AWS::Service::Type` representations. Case-fold the service and type segments and remove every non-alphanumeric character from each segment before lookup.

- [ ] **Step 4: Implement identifier and tag handling**

- ARN-backed resources use the full ARN as `external_id`.
- Non-ARN identifiers use the complete Identifier cell.
- Name precedence is `Name` tag, final ARN segment, Identifier.
- Base columns are never interpreted as tags.
- Every non-empty unknown column after the base set becomes an original tag key.
- Omit a tag whose normalized key matches the sensitive-key set from Task 2 and append `sensitive_tag_omitted`.
- CSV Formula prefixes are treated as plain text and are never executed or rewritten.
- Every emitted resource uses `source_profile="aws.resource_explorer.csv.v1"`, `source_priority=100`, `completeness=partial`, and `detail_level=summary`.

- [ ] **Step 5: Register with confidence-based detection**

Return confidence 100 only when all required headers exist. Return confidence 0 for JSON or ZIP/XLSX signatures.

- [ ] **Step 6: Run and commit**

```bash
uv run pytest tests/unit/ingest/test_aws_resource_explorer.py -q
git add src/cloud_inventory/ingest/parsers tests/unit/ingest/test_aws_resource_explorer.py tests/fixtures/aws
git commit -m "feat: parse AWS Resource Explorer exports"
```

---

### Task 5: NAVER Cloud Platform XLSX Parsers

**Files:**

- Create: `src/cloud_inventory/ingest/parsers/ncp_console.py`
- Modify: `src/cloud_inventory/ingest/parsers/registry.py`
- Create: `tests/unit/ingest/test_ncp_console.py`
- Modify: `tests/conftest.py`

**Interfaces:**

- Produces: `NcpServerXlsxParser`
- Produces: `NcpPublicIpXlsxParser`
- Produces: `NcpLoadBalancerXlsxParser`
- Produces: `NcpBucketXlsxParser`
- Consumes: common Parser contract and domain models

- [ ] **Step 1: Add a synthetic XLSX fixture builder**

The helper must create in-memory workbooks with Korean or English headers and synthetic rows:

```python
def write_xlsx(path: Path, headers: list[str], rows: list[list[object]]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path
```

- [ ] **Step 2: Write failing profile tests**

Test exact header aliases:

```python
SERVER_HEADERS = {
    "name": {"Server Name", "서버 이름"},
    "id": {"Instance ID", "인스턴스 ID"},
    "status": {"Status", "상태"},
    "region": {"Region", "리전"},
    "zone": {"Zone", "존"},
    "vpc": {"VPC", "VPC 이름"},
    "subnet": {"Subnet", "Subnet 이름"},
    "private_ip": {"Private IP", "사설 IP"},
    "public_ip": {"Public IP", "공인 IP"},
}
```

Define these additional exact alias sets:

```python
PUBLIC_IP_HEADERS = {
    "public_ip": {"Public IP", "공인 IP"},
    "status": {"Status", "상태"},
    "applied_server": {"Applied Server", "적용 서버"},
    "private_ip": {"Private IP", "사설 IP"},
    "vpc": {"VPC", "VPC 이름"},
    "region": {"Region", "리전"},
}

LOAD_BALANCER_HEADERS = {
    "name": {"Load Balancer Name", "로드 밸런서 이름"},
    "id": {"Instance ID", "인스턴스 ID"},
    "status": {"Status", "상태"},
    "type": {"Type", "유형"},
    "network": {"Network", "네트워크"},
    "vpc": {"VPC", "VPC 이름"},
    "subnet": {"Subnet", "Subnet 이름"},
    "ip": {"IP", "IP 주소"},
    "region": {"Region", "리전"},
}

BUCKET_HEADERS = {
    "name": {"Name", "Bucket Name", "버킷 이름"},
    "region": {"Region", "리전"},
    "size": {"Size", "사용량"},
    "created_at": {"Date Created", "생성 일시"},
}
```

Tests must cover profile ambiguity, missing identifier, Korean headers, English headers, empty rows, formula cells, external links, and ZIP expansion limits.

- [ ] **Step 3: Reject unsafe Workbook features before loading**

Inspect the ZIP entries before `openpyxl`:

- Reject any entry ending in `vbaProject.bin`.
- Reject any entry under `xl/externalLinks/`.
- Reject a worksheet XML entry containing a formula element in the SpreadsheetML namespace.
- Reject encrypted files that are not valid ZIP workbooks.
- Reject a workbook when total uncompressed ZIP size exceeds 500 MB, one entry exceeds 250 MB, or any non-empty entry has an expansion ratio above 100.
- Open with `read_only=True`, `data_only=True`, `keep_links=False`.
- Read only the first non-empty sheet.

- [ ] **Step 4: Implement NCP resource normalization**

- `realm` and `account_id` always come from upload metadata.
- `region` comes from the row, then metadata, and is required.
- Create one Region resource for each distinct Region and one Zone resource for each non-empty Zone, using the exact exported value as external ID and name.
- Add `contains` from Region to Zone only when both values are present in the same row.
- Server rows create a `virtual_machine` and IP child resources for non-empty IP cells. Use IP external IDs `server:{server_instance_id}:{private|public}:{address}` and store the parseable address in `attributes.address`.
- Public IP rows create an `ip_address` with external ID `public:{address}` and an `assigned_to` relation only when the server Instance ID is present.
- Load Balancer rows create `load_balancer` resources with network values in `attributes`.
- Bucket rows create `object_bucket` resources and never enumerate objects.
- VPC and Subnet names without IDs remain attributes and do not create inferred relationships.
- Every resource has `completeness=partial` and `detail_level=summary`.
- Every emitted resource uses its registered `source_profile` and `source_priority=200`.

- [ ] **Step 5: Register all four profiles**

Use exact profile IDs:

```text
ncp.server_list.xlsx.v1
ncp.public_ip_list.xlsx.v1
ncp.load_balancer_list.xlsx.v1
ncp.object_storage_bucket_list.xlsx.v1
```

Each profile returns confidence 100 only when its identifier and discriminator headers are present.

- [ ] **Step 6: Run and commit**

```bash
uv run pytest tests/unit/ingest/test_ncp_console.py -q
git add src/cloud_inventory/ingest/parsers tests/unit/ingest/test_ncp_console.py tests/conftest.py
git commit -m "feat: parse NCP console workbooks"
```

---

### Task 6: File Validation and Artifact Storage

**Files:**

- Create: `src/cloud_inventory/ingest/file_validation.py`
- Create: `src/cloud_inventory/ingest/artifact_store.py`
- Create: `tests/unit/ingest/test_file_validation.py`
- Create: `tests/unit/ingest/test_artifact_store.py`

**Interfaces:**

- Produces: `ValidatedFile`
- Produces: `validate_upload(stream, filename, max_bytes) -> ValidatedFile`
- Produces: `ArtifactStore.put(validated_file, object_key) -> StoredArtifact`
- Produces: `ArtifactStore.open(object_key) -> AsyncContextManager[BinaryIO]`
- Produces: `ArtifactStore.delete(object_key) -> None`

- [ ] **Step 1: Write failing file validation tests**

Test:

- 100 MB accepted and 100 MB plus one byte rejected using a generated stream.
- CSV detected from decoded text, not extension alone.
- JSON detected from the first non-whitespace byte.
- XLSX detected from ZIP structure and required workbook entries.
- `.xls`, macro XLSX, encrypted XLSX, executable, and path-traversal filename rejected.
- SHA-256 and byte count returned.

- [ ] **Step 2: Implement streaming validation**

`validate_upload` must stream into `tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)` in 1 MB chunks, stop immediately above the configured limit, compute SHA-256 during the copy, sanitize the basename, and return:

```python
@dataclass
class ValidatedFile:
    stream: BinaryIO
    original_filename: str
    media_type: Literal["text/csv", "application/json", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]
    size_bytes: int
    sha256: str
```

Rewind the returned stream to byte zero. Close the temporary stream on every rejected upload and after Artifact Store persistence.

- [ ] **Step 3: Define Artifact Store implementations**

Implement:

- `FileSystemArtifactStore` rooted at `Settings.artifact_root`.
- `InMemoryArtifactStore` for tests.
- Both implementations provide idempotent `delete`; deleting an absent key succeeds.

Object keys use `imports/{import_id}/{source_file_id}/{sha256}` and never include the original filename.
`FileSystemArtifactStore` rejects absolute paths, `..`, symlinks, and any resolved path outside the configured root. It creates the root with mode `0700`, writes with mode `0600` to a sibling temporary file, calls `fsync`, atomically renames it, and opens stored files read-only.

- [ ] **Step 4: Test upload and readback**

Run: `uv run pytest tests/unit/ingest/test_file_validation.py tests/unit/ingest/test_artifact_store.py -q`

Expected: PASS with the in-memory implementation and a temporary-directory filesystem implementation.

- [ ] **Step 5: Commit**

```bash
git add src/cloud_inventory/ingest tests/unit/ingest
git commit -m "feat: validate and store import artifacts"
```

---

### Task 7: Control Database and PostgreSQL Job Queue

**Files:**

- Create: `src/cloud_inventory/persistence/base.py`
- Create: `alembic.ini`
- Create: `src/cloud_inventory/persistence/models.py`
- Create: `src/cloud_inventory/persistence/session.py`
- Create: `src/cloud_inventory/persistence/repositories.py`
- Create: `src/cloud_inventory/persistence/migrations/env.py`
- Create: `src/cloud_inventory/persistence/migrations/versions/0001_control_tables.py`
- Create: `src/cloud_inventory/jobs/queue.py`
- Create: `tests/unit/jobs/test_queue.py`
- Create: `tests/integration/test_postgres_queue.py`

**Interfaces:**

- Produces: `ImportRepository`
- Produces: `JobQueue.enqueue(job_type, payload, idempotency_key) -> UUID`
- Produces: `JobQueue.claim(worker_id) -> ClaimedJob | None`
- Produces: `JobQueue.succeed(job_id, result)` and `JobQueue.fail(job_id, error)`

- [ ] **Step 1: Write failing Repository state tests**

Use these state transitions:

```text
SourceFile: uploaded -> parsing -> preview_ready -> applying -> applied
SourceFile failure: uploaded or parsing or applying -> failed
SourceFile artifact: available -> expired
CollectionJob: queued -> running -> succeeded
CollectionJob failure: running -> retry_wait -> queued -> failed
ImportPreview: ready -> applying -> applied
ImportPreview expiry: ready -> expired
```

Reject all other transitions.

- [ ] **Step 2: Define database models**

Create:

- `ImportRequest`: id, provider, realm, account_id, export_type, region, exported_at, request_fingerprint, created_at, created_by
- `SourceFile`: id, import_id, filename, media_type, sha256, deduplication_key, size_bytes, artifact_key, parser_profile, status, artifact_status, expires_at
- `ImportPreview`: id, import_id, batch_hash, parser_versions JSON, summary JSON, expires_at, status
- `PreviewChange`: id, preview_id, ordinal, cloud_uid, resource_type, action, changed_fields JSON, warning_codes JSON, desired JSON
- `CollectionJob`: id, job_type, payload JSON, status, attempts, available_at, locked_at, locked_by, last_error
- `CollectionRun`: id, import_id, batch_hash, apply_valid_only, idempotency_key, status, checkpoint, summary JSON, started_at, finished_at
- `ChangeSummary`: id, run_id, cloud_uid, action, changed_fields JSON, warning_codes JSON
- `TagMapping`: id, provider, realm, account_id, source_key, source_key_normalized, source_value, business_service_code, priority, enabled
- `OwnerMapping`: id, provider, realm, account_id, source_value, netbox_owner_id, priority, enabled

Add these database constraints:

```text
SourceFile: unique(deduplication_key)
ImportRequest: unique(request_fingerprint)
CollectionJob: unique(idempotency_key)
CollectionRun: unique(idempotency_key)
PreviewChange: unique(preview_id, cloud_uid), unique(preview_id, ordinal)
TagMapping active partial unique index:
  provider, realm, account_id, source_key_normalized, source_value
OwnerMapping active partial unique index:
  provider, realm, account_id, source_value
```

Both mapping indexes use `WHERE enabled = true`. Normalize `source_key_normalized` with Unicode `casefold()` and surrounding whitespace trim before persistence. Preserve the original key and compare mapping values exactly after surrounding whitespace trim. Require `priority` in the range 0 through 1000 and default it to 100.

Insert ImportPreview and its PreviewChange rows in one transaction ordered by `cloud_uid`. Treat preview content, parser versions, summary, and changes as immutable after insert; only the preview status may transition.

Build `SourceFile.deduplication_key` as SHA-256 over the UTF-8 bytes of `provider`, `realm`, `account_id`, and file SHA-256 joined by a null byte. This makes duplicate detection account-scoped across separate Import requests without relying on a cross-table unique constraint.

Build `ImportRequest.request_fingerprint` as SHA-256 over `provider`, `realm`, `account_id`, and the sorted list of uploaded file SHA-256 values. An exact repeated file set returns the original Import. A request that mixes an account-scoped duplicate file with a new file returns `409 duplicate_file_in_different_import`; it does not silently reuse a SourceFile owned by another Import.

- [ ] **Step 3: Implement atomic job claiming**

The claim query must use:

```python
stmt = (
    select(CollectionJob)
    .where(
        CollectionJob.status == JobStatus.QUEUED,
        CollectionJob.available_at <= now,
    )
    .order_by(CollectionJob.created_at)
    .with_for_update(skip_locked=True)
    .limit(1)
)
```

Within the same transaction set `running`, `locked_by`, `locked_at`, and increment attempts.

- [ ] **Step 4: Test two concurrent workers against PostgreSQL**

Start PostgreSQL 16 through testcontainers. Enqueue one job, race two async claims, and assert exactly one worker receives it.

Also assert:

- Same idempotency key returns the existing job.
- Transient attempts 1 and 2 schedule retries after 5 and 30 seconds.
- Attempt 3 marks the job failed.
- File validation, unknown Parser profile, schema validation, and preview policy errors fail permanently on the first attempt.
- A lock older than 15 minutes is recoverable.

- [ ] **Step 5: Run migrations and tests**

```bash
uv run alembic upgrade head
uv run pytest tests/unit/jobs tests/integration/test_postgres_queue.py -q
```

- [ ] **Step 6: Commit**

```bash
git add alembic.ini src/cloud_inventory/persistence src/cloud_inventory/jobs tests/unit/jobs tests/integration/test_postgres_queue.py
git commit -m "feat: add PostgreSQL import job queue"
```

---

### Task 8: NetBox 4.6.5 Stack and Portable Schema

**Files:**

- Create: `compose.yaml`
- Create: `deploy/netbox/Dockerfile`
- Create: `deploy/inventory/Dockerfile`
- Create: `deploy/netbox/plugin_requirements.txt`
- Create: `deploy/netbox/configuration/plugins.py`
- Create: `deploy/netbox/configuration/extra.py`
- Create: `schemas/netbox/custom-objects-v1.json`
- Create: `scripts/apply_netbox_schema.py`
- Create: `src/cloud_inventory/netbox/bootstrap.py`
- Create: `tests/integration/test_netbox_schema.py`

**Interfaces:**

- Produces: local NetBox at `http://localhost:8000`
- Produces: local Inventory API at `http://localhost:8080`
- Produces: `bootstrap_core_fields(client) -> None`
- Produces: `apply_custom_object_schema(client, schema) -> SchemaApplyResult`

- [ ] **Step 1: Define the local Docker Compose topology**

Pin:

```text
netboxcommunity/netbox:v4.6.5-5.0.1
netboxlabs-netbox-custom-objects==0.6.0
postgres:16-alpine
redis:7-alpine
```

Services:

```text
netbox
netbox-worker
netbox-postgres
netbox-redis
netbox-redis-cache
inventory-api
inventory-worker
inventory-migrate
control-postgres
```

Expose only NetBox 8000 and Inventory API 8080 to `127.0.0.1`. Mount one named volume, `inventory-artifacts`, read-write into `inventory-api` and `inventory-worker` at `/var/lib/cloud-inventory/artifacts`. Use health checks before dependent services start.

The ignored `.env` supplies PostgreSQL passwords, NetBox `SECRET_KEY`, `API_TOKEN_PEPPER_1`, `SUPERUSER_PASSWORD`, `SUPERUSER_API_KEY`, `SUPERUSER_API_TOKEN`, Inventory CSRF secret, and the Inventory database URL. Configure the Inventory client token as `nbt_<SUPERUSER_API_KEY>.<SUPERUSER_API_TOKEN>` and send it with the v2 `Bearer` authorization scheme. No secret value is present in Compose or `.env.example`.

`deploy/inventory/Dockerfile` copies `/uv` from `ghcr.io/astral-sh/uv:0.11.32` into a `python:3.12-slim-bookworm` image and installs with `uv sync --frozen --no-dev`. It creates UID and GID 10001, prepares `/var/lib/cloud-inventory/artifacts` for that identity, and runs as the non-root user with `/app/.venv/bin` first on PATH. Compose runs `alembic upgrade head` in the one-shot `inventory-migrate` service, waits for its successful completion, then runs `uvicorn cloud_inventory.app:create_app --factory --host 0.0.0.0 --port 8080` for the API and `python -m cloud_inventory.jobs.worker` for the Worker.

- [ ] **Step 2: Build NetBox with Custom Objects**

`deploy/netbox/Dockerfile`:

```dockerfile
FROM netboxcommunity/netbox:v4.6.5-5.0.1
COPY deploy/netbox/plugin_requirements.txt /opt/netbox/plugin_requirements.txt
RUN /opt/netbox/venv/bin/pip install --no-cache-dir -r /opt/netbox/plugin_requirements.txt
```

`plugins.py` must add `netbox_custom_objects` to `PLUGINS`. Do not place tokens or passwords in committed configuration.

- [ ] **Step 3: Define the Portable Schema**

Create these Custom Object Types:

```text
cloud_account
cloud_network_interface
cloud_load_balancer
managed_database
object_bucket
domain
dns_zone
dns_record
business_service
```

Every cloud-backed type has:

```text
1  name: text, required, primary
2  cloud_uid: text, required, unique, ui_editable=no
3  external_id: text, required, ui_editable=no
4  provider: select, choice_set="Cloud Provider", required, ui_editable=no
5  realm: select, choice_set="Cloud Realm", required, ui_editable=no
6  account_id: text, required, ui_editable=no
7  region_name: text, required, ui_editable=no
8  status: text, ui_editable=no
9  last_seen_at: datetime, required, ui_editable=no
10 sync_state: select, choice_set="Cloud Sync State", required, ui_editable=no
11 source_tags: json, ui_editable=no
12 source_attributes: json, ui_editable=no
```

Field IDs are stable per Custom Object Type. Assign the common IDs above and continue from 13 in the exact order below. Never reuse an ID after a field is removed.

| Type | Fields after the common fields |
|---|---|
| `cloud_account` | `13 collection_mode:text`, `14 console_url:url`, `15 last_success_at:datetime`, `16 last_run_status:text` |
| `cloud_network_interface` | `13 cloud_account:object custom-objects/cloud_account required protect`, `14 region:object dcim/region set_null`, `15 zone:object dcim/site set_null`, `16 vpc:object ipam/vrf set_null`, `17 subnet:object ipam/prefix set_null`, `18 mac_address:text`, `19 private_ips:json`, `20 public_ips:json`, `21 attachment_status:text`, `22 attached_virtual_machine:object virtualization/virtualmachine set_null`, `23 vm_interface:object virtualization/vminterface set_null` |
| `cloud_load_balancer` | `13 cloud_account:object custom-objects/cloud_account required protect`, `14 load_balancer_type:text`, `15 scheme:text`, `16 vpc:object ipam/vrf set_null`, `17 subnets:multiobject ipam/prefix`, `18 dns_name:text`, `19 frontend_ips:multiobject ipam/ipaddress`, `20 listeners:json`, `21 target_groups:json`, `22 backend_resources:polymorphic multiobject` |
| `managed_database` | `13 cloud_account:object custom-objects/cloud_account required protect`, `14 engine:text`, `15 engine_version:text`, `16 topology:text`, `17 vpc:object ipam/vrf set_null`, `18 subnets:multiobject ipam/prefix`, `19 endpoint:text`, `20 port:integer`, `21 public_access:boolean`, `22 high_availability:boolean`, `23 multi_zone:boolean`, `24 encrypted:boolean`, `25 backup_enabled:boolean` |
| `object_bucket` | `13 cloud_account:object custom-objects/cloud_account required protect`, `14 region:object dcim/region set_null`, `15 versioning:boolean`, `16 encryption:text`, `17 public_access:boolean`, `18 object_lock:boolean`, `19 console_url:url` |
| `domain` | `13 cloud_account:object custom-objects/cloud_account required protect`, `14 registrar:text`, `15 registered_at:datetime`, `16 expires_at:datetime`, `17 auto_renew:boolean`, `18 name_servers:json`, `19 owner_hint:text` |
| `dns_zone` | `13 cloud_account:object custom-objects/cloud_account required protect`, `14 visibility:text`, `15 vpc_links:multiobject ipam/vrf`, `16 name_servers:json`, `17 record_count:integer` |
| `dns_record` | `13 zone:object custom-objects/dns_zone required protect`, `14 record_type:text required`, `15 values:json required`, `16 ttl:integer`, `17 alias_target:text`, `18 related_resources:polymorphic multiobject` |

For `cloud_load_balancer.backend_resources`, allow:

```text
virtualization/virtualmachine
ipam/ipaddress
custom-objects/managed_database
custom-objects/object_bucket
```

For `dns_record.related_resources`, allow:

```text
virtualization/virtualmachine
ipam/ipaddress
custom-objects/cloud_load_balancer
custom-objects/managed_database
custom-objects/object_bucket
```

`business_service` is user-managed rather than cloud-backed and uses this separate field sequence:

```text
1 name: text, required, primary
2 service_code: text, required, unique
3 environment: text
4 criticality: text
5 status: text
6 runbook_url: url
7 repository_url: url
8 resources: polymorphic multiobject
```

Allow these types for `business_service.resources`:

```text
dcim/region
dcim/site
ipam/vrf
ipam/prefix
ipam/ipaddress
virtualization/virtualmachine
virtualization/vminterface
custom-objects/cloud_account
custom-objects/cloud_network_interface
custom-objects/cloud_load_balancer
custom-objects/managed_database
custom-objects/object_bucket
custom-objects/domain
custom-objects/dns_zone
custom-objects/dns_record
```

All three polymorphic fields set `is_polymorphic=true` and use the exact `related_object_types` lists above. Single-object account and parent fields use `on_delete_behavior="protect"`. Other single-object relations use `on_delete_behavior="set_null"`. Order the COT definitions as `cloud_account`, `cloud_network_interface`, `managed_database`, `object_bucket`, `cloud_load_balancer`, `domain`, `dns_zone`, `dns_record`, `business_service` so new-type dependencies remain acyclic.

- [ ] **Step 4: Bootstrap Core Custom Fields**

Idempotently create these Custom Field Choice Sets before applying the Portable Schema:

```text
Cloud Provider: aws, ncp
Cloud Realm: commercial, government
Collection Source: export, api
Cloud Sync State: current, warning, error, stale_candidate, inactive
```

Create Core custom fields `cloud_uid`, `provider`, `realm`, `account_id`, `external_id`, `collection_source`, `cloud_status`, `last_seen_at`, `sync_state`, `source_tags`, and `source_attributes` for:

```text
dcim.region
dcim.site
ipam.vrf
ipam.prefix
ipam.ipaddress
virtualization.virtualmachine
virtualization.vminterface
```

Use the Choice Sets above for `provider`, `realm`, `collection_source`, and `sync_state`. Define `source_tags` and `source_attributes` as JSON and the remaining added field `cloud_status` as text. Set all eleven fields to `ui_editable="no"` and leave them non-required so the bootstrap can run against an existing NetBox installation. The Writer treats them as required for collector-created objects. Re-running bootstrap must produce no changes.

Use `/api/extras/custom-field-choice-sets/` for Choice Sets and `/api/extras/custom-fields/` for Core Custom Fields. Lookup by exact name, compare normalized choices and assigned object types, create when absent, PATCH only non-destructive differences, and fail on a conflicting type or choice value.

- [ ] **Step 5: Preview and apply schema**

`scripts/apply_netbox_schema.py` must:

1. Read the committed JSON.
2. Call Core Choice Set and Custom Field bootstrap.
3. POST the schema document itself to `/api/plugins/custom-objects/schema/preview/`.
4. Abort when any item in `response.diffs` has `has_destructive_changes=true`.
5. POST `{"allow_destructive": false, "schema": schema_document}` to `/api/plugins/custom-objects/schema/apply/`.
6. Print created, changed, and unchanged counts.

- [ ] **Step 6: Add a schema integration test**

Run:

```bash
docker compose up -d netbox netbox-worker
uv run python scripts/apply_netbox_schema.py
uv run python scripts/apply_netbox_schema.py
uv run pytest tests/integration/test_netbox_schema.py -q
```

Expected: second schema application reports zero destructive changes and zero schema changes.

- [ ] **Step 7: Commit**

```bash
git add compose.yaml deploy schemas/netbox scripts/apply_netbox_schema.py src/cloud_inventory/netbox/bootstrap.py tests/integration/test_netbox_schema.py
git commit -m "feat: provision NetBox cloud inventory schema"
```

---

### Task 9: Reconciliation and NetBox Writer

**Files:**

- Create: `src/cloud_inventory/reconciliation/fingerprint.py`
- Create: `src/cloud_inventory/reconciliation/diff.py`
- Create: `src/cloud_inventory/netbox/client.py`
- Create: `src/cloud_inventory/netbox/writer.py`
- Create: `tests/unit/reconciliation/test_diff.py`
- Create: `tests/unit/netbox/test_client.py`
- Create: `tests/unit/netbox/test_writer.py`

**Interfaces:**

- Produces: `compute_fingerprint(resource) -> str`
- Produces: `Reconciler.preview(batch, current) -> PreviewResult`
- Produces: `NetBoxClient`
- Produces: `NetBoxWriter.apply(preview, checkpoint) -> ApplyResult`

- [ ] **Step 1: Write failing fingerprint and diff tests**

Assert:

- Tag and JSON key order does not affect a fingerprint.
- User fields are excluded.
- Empty and `unknown` incoming values do not clear existing values.
- Same fingerprint produces `unchanged`.
- Missing current object produces `create`.
- Changed cloud field produces `update`.
- Objects absent from a manual Batch never produce delete or inactive actions.
- Repeating the same Batch produces only unchanged actions.

- [ ] **Step 2: Define Preview types**

```python
class ChangeAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    WARNING = "warning"
    ERROR = "error"


class ResourceChange(BaseModel):
    cloud_uid: str
    resource_type: ResourceType
    action: ChangeAction
    changed_fields: list[str]
    warnings: list[str]
    desired: CloudResource


class PreviewResult(BaseModel):
    batch_hash: str
    created: int
    updated: int
    unchanged: int
    warnings: int
    errors: int
    changes: list[ResourceChange]
```

- [ ] **Step 3: Implement the HTTPX NetBox client**

Required methods:

```python
async def get_by_cloud_uid(self, resource_type: ResourceType, cloud_uid: str) -> NetBoxObject | None
async def create(self, resource_type: ResourceType, payload: dict[str, JsonValue]) -> NetBoxObject
async def update(self, resource_type: ResourceType, object_id: int, payload: dict[str, JsonValue], etag: str) -> NetBoxObject
async def resolve_relation(self, cloud_uid: str) -> NetBoxObject | None
```

Use `Authorization: Bearer <v2-token>`, a 10 second timeout, bounded retry for 429 and 5xx, `Retry-After` when present, and redact Authorization headers from errors.

After a list lookup identifies one object, GET its detail endpoint and retain the response ETag. PATCH with `If-Match`. On `412`, refetch and recompute the managed-field diff once; if it still conflicts, stop that resource with `concurrent_update` rather than overwriting the other writer.

Use this endpoint map:

```python
NETBOX_ENDPOINTS = {
    ResourceType.CLOUD_ACCOUNT: "/api/plugins/custom-objects/cloud_account/",
    ResourceType.REGION: "/api/dcim/regions/",
    ResourceType.ZONE: "/api/dcim/sites/",
    ResourceType.VPC: "/api/ipam/vrfs/",
    ResourceType.SUBNET: "/api/ipam/prefixes/",
    ResourceType.VIRTUAL_MACHINE: "/api/virtualization/virtual-machines/",
    ResourceType.NETWORK_INTERFACE: "/api/plugins/custom-objects/cloud_network_interface/",
    ResourceType.IP_ADDRESS: "/api/ipam/ip-addresses/",
    ResourceType.LOAD_BALANCER: "/api/plugins/custom-objects/cloud_load_balancer/",
    ResourceType.MANAGED_DATABASE: "/api/plugins/custom-objects/managed_database/",
    ResourceType.OBJECT_BUCKET: "/api/plugins/custom-objects/object_bucket/",
    ResourceType.DOMAIN: "/api/plugins/custom-objects/domain/",
    ResourceType.DNS_ZONE: "/api/plugins/custom-objects/dns_zone/",
    ResourceType.DNS_RECORD: "/api/plugins/custom-objects/dns_record/",
}
```

Lookup Core objects through `cf_cloud_uid`. Lookup Custom Objects through their `cloud_uid` field. Encode list query values through HTTPX query parameters rather than string concatenation.

- [ ] **Step 4: Implement dependency-ordered writes**

Write in this exact order:

```text
1 cloud_account, region, zone
2 vpc, subnet
3 virtual_machine, network_interface, VMInterface
4 ip_address
5 load_balancer, managed_database, object_bucket
6 domain, dns_zone, dns_record
7 approved BusinessService and Owner mapping
8 remaining relationships
9 last_seen_at and sync_state
```

After each stage, save the stage number as the run checkpoint. If a parent fails, mark dependants `blocked_by_dependency`. Do not compensate by deleting successful objects.

Use these Core materialization rules:

```text
region -> Region name and deterministic slug
zone -> Site name, deterministic slug, status=active, optional Region
vpc -> VRF name
subnet -> Prefix only when attributes.cidr is a valid IPv4 or IPv6 network
virtual_machine -> VirtualMachine name and mapped native status
network_interface -> CloudNetworkInterface always; VMInterface only when attached VM resolves
ip_address -> IPAddress only when attributes.address or external_id is a parseable address
```

Build deterministic slugs as `slugify(name)[:80] + "-" + sha256(cloud_uid)[:12]`. For an IP without a prefix length, append `/32` for IPv4 or `/128` for IPv6. Map native VirtualMachine status as `provisioning -> staged`, `active -> active`, `stopped -> offline`, `degraded -> active`, `failed -> failed`, `deleting -> decommissioning`, `inactive -> offline`, and `unknown -> offline`; always preserve the normalized cloud status in `cf_cloud_status`.

When a Core model lacks a required structural value, produce a preview warning with code `unmaterializable_summary`, retain the resource in ChangeSummary, and skip the NetBox write. Never invent CIDRs, addresses, Regions, Zones, VPCs, Subnets, or relationships from names alone.

For every cloud-backed Custom Object, resolve and set its required `cloud_account` field from the resource provider, realm, and account ID. For Core objects, write those three values to Custom Fields instead of a direct CloudAccount relation.

- [ ] **Step 5: Resolve approved BusinessService and Owner mappings**

Read enabled `TagMapping` and `OwnerMapping` for the resource provider, realm, and account:

```text
Service tag keys: Service, service, application, app
Owner tag keys: Owner, owner, team, managed-by
key comparison: case-insensitive
value comparison: exact after surrounding whitespace trim
```

A TagMapping may link only to an existing `business_service` Custom Object selected by its unique `service_code`. An OwnerMapping may link only to an existing NetBox Owner returned by `/api/users/owners/{id}/`. Sort matching mappings by `priority` descending and UUID ascending. Equal highest-priority mappings pointing to different targets produce `ambiguous_mapping` and attach neither target. Missing or disabled mappings preserve `service_hint` or `owner_hint`, add an unresolved warning, and never create a BusinessService, Owner, user, or group.

An approved TagMapping adds the resource to `business_service.resources` and never removes existing members. An approved OwnerMapping sets the NetBox `owner` field only when it is empty or already equals the mapped Owner. If a different Owner is already present, preserve it and record `owner_conflict`.

- [ ] **Step 6: Preserve user-managed fields**

The Writer may send only fields owned by the collector:

```text
cloud_uid
provider
realm
account_id
external_id
cloud_status
collection_source
source_tags
source_attributes
last_seen_at
sync_state
cloud-managed relations
```

It must never send description, comments, owner, business service manual links, runbook URL, or repository URL unless an approved mapping supplies the relation under Step 5. Mapping writes follow the additive and conflict-preserving rules from Step 5.

- [ ] **Step 7: Test with mocked NetBox responses**

Use `respx` to assert endpoints, payloads, call order, ETag use, one-time `412` reconciliation, retries, the absence of secrets, disabled mappings, missing Owner IDs, and approved mapping attachment.

- [ ] **Step 8: Run and commit**

```bash
uv run pytest tests/unit/reconciliation tests/unit/netbox -q
git add src/cloud_inventory/reconciliation src/cloud_inventory/netbox tests/unit/reconciliation tests/unit/netbox
git commit -m "feat: reconcile and write NetBox resources"
```

---

### Task 10: Import API and Immutable Preview

**Files:**

- Create: `src/cloud_inventory/api/dependencies.py`
- Create: `src/cloud_inventory/api/imports.py`
- Create: `src/cloud_inventory/api/mappings.py`
- Create: `src/cloud_inventory/api/runs.py`
- Modify: `src/cloud_inventory/app.py`
- Create: `tests/api/test_imports.py`
- Create: `tests/api/test_mappings.py`

**Interfaces:**

- Produces: `POST /imports`
- Produces: `GET /imports/{import_id}/preview`
- Produces: `POST /imports/{import_id}/apply`
- Produces: `GET /runs/{run_id}`
- Produces: `GET /mappings/tags`
- Produces: `PUT /mappings/tags/{mapping_id}`
- Produces: `GET /mappings/owners`
- Produces: `PUT /mappings/owners/{mapping_id}`
- Consumes: ArtifactStore, ImportRepository, JobQueue

- [ ] **Step 1: Write failing upload API tests**

Use FastAPI dependency overrides for in-memory Artifact Store, fake Repository, and fake Job Queue.

Assert:

- Multipart upload returns `202` with import ID and parse job ID.
- More than 20 files returns `422`.
- Oversized file returns `413`.
- Duplicate file returns the original Import result.
- A mix of an existing account-scoped file and a new file returns `409`.
- Provider, realm, account ID, and exported time are required.
- Exported time more than five minutes in the future returns `422`.
- Filename and content type mismatch returns `415`.

- [ ] **Step 2: Implement `POST /imports`**

Accept:

```python
files: Annotated[list[UploadFile], File()]
provider: Annotated[Provider, Form()]
realm: Annotated[Realm, Form()]
account_id: Annotated[str, Form(min_length=1, max_length=128)]
export_type: Annotated[str, Form(min_length=1, max_length=64)]
exported_at: Annotated[datetime, Form()]
region: Annotated[str | None, Form(max_length=64)] = None
```

Allow `export_type="auto"` or one of the six registered Parser profile IDs. The UI defaults to `auto`, which permits a single Import to combine several NCP service workbook types. Reject any other value with `422`.

Validate and hash all files before opening the database transaction. Compute the request fingerprint, then:

1. Return `200` and the original Import result when that fingerprint already exists.
2. Return `409 duplicate_file_in_different_import` when any file deduplication key exists under another fingerprint.
3. Create the Import, SourceFiles, and Jobs atomically.

For every new file:

1. Store the already validated artifact.
2. Persist SourceFile.

After all SourceFiles are persisted, enqueue exactly one `parse_import` job with payload `{"import_id": import_id}` and idempotency key `parse:{import_id}:{request_fingerprint}`. The handler parses every SourceFile in that Import in SourceFile ID order and combines their Batches once; do not enqueue one parse job per file.

On a persistence or queue failure, delete artifacts already written for that request before returning an error. Artifact deletion is idempotent.

- [ ] **Step 3: Write failing preview and apply tests**

Assert:

- Preview before parsing returns `409`.
- Ready preview contains counts and paginated changes.
- Expired preview returns `410`.
- Applying a preview whose Batch hash changed returns `409`.
- Errors block apply unless `apply_valid_only=true`.
- A second apply request returns the existing run ID.

- [ ] **Step 4: Implement preview and apply endpoints**

`GET /imports/{id}/preview` accepts `offset` default 0 and `limit` default 100, maximum 500, and returns:

```json
{
  "import_id": "uuid",
  "batch_hash": "sha256",
  "expires_at": "2026-07-29T00:00:00Z",
  "summary": {
    "create": 10,
    "update": 2,
    "unchanged": 4,
    "warning": 1,
    "error": 0
  },
  "total_changes": 16,
  "offset": 0,
  "limit": 100,
  "changes": []
}
```

`POST /imports/{id}/apply` requires the client to send `batch_hash` and `apply_valid_only`. In one database transaction, it creates a queued CollectionRun and enqueues `apply_import` with payload `{"run_id": run_id}`. Use idempotency key `apply:{import_id}:{batch_hash}:{apply_valid_only}`, formatting the final value as lower-case `true` or `false`, for both records. It returns the existing run ID for a repeated request and never applies inline.

- [ ] **Step 5: Implement run status**

`GET /runs/{run_id}` returns status, checkpoint, counts, started time, finished time, and sanitized error summaries.

- [ ] **Step 6: Write failing Mapping API tests**

Test:

- Client-selected UUID provides idempotent create and update.
- Tag mapping requires provider, realm, account ID, source key, source value, and BusinessService `service_code`.
- Owner mapping requires provider, realm, account ID, source value, and positive NetBox Owner ID.
- Disabled mappings remain queryable but are not returned by the active Repository query.
- Duplicate active source key and value for one account returns `409`.

- [ ] **Step 7: Implement Mapping endpoints**

Request bodies:

```python
class TagMappingRequest(BaseModel):
    provider: Provider
    realm: Realm
    account_id: str
    source_key: str
    source_value: str
    business_service_code: str
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True


class OwnerMappingRequest(BaseModel):
    provider: Provider
    realm: Realm
    account_id: str
    source_value: str
    netbox_owner_id: int = Field(gt=0)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True
```

Return `201` for first upsert and `200` for an update. List endpoints support provider, realm, account ID, enabled, offset, and limit filters. Before enabling a mapping, resolve the target through NetBox and return `422` when the BusinessService or Owner does not exist.

- [ ] **Step 8: Run and commit**

```bash
uv run pytest tests/api/test_imports.py tests/api/test_mappings.py -q
git add src/cloud_inventory/api src/cloud_inventory/app.py tests/api/test_imports.py tests/api/test_mappings.py
git commit -m "feat: expose manual import workflow API"
```

---

### Task 11: Worker Orchestration and Minimal Upload UI

**Files:**

- Create: `src/cloud_inventory/jobs/worker.py`
- Create: `src/cloud_inventory/api/ui.py`
- Create: `src/cloud_inventory/api/templates/imports.html`
- Create: `src/cloud_inventory/api/templates/preview.html`
- Modify: `src/cloud_inventory/app.py`
- Create: `tests/unit/jobs/test_worker.py`
- Create: `tests/api/test_ui.py`

**Interfaces:**

- Produces: `run_worker(settings) -> NoReturn`
- Produces: `handle_parse_import(job) -> None`
- Produces: `handle_apply_import(job) -> None`
- Produces: `expire_artifacts(now: datetime) -> int`
- Produces: `GET /ui/imports`
- Produces: `POST /ui/imports`
- Produces: `GET /ui/imports/{import_id}`
- Produces: `POST /ui/imports/{import_id}/apply`

- [ ] **Step 1: Write failing Worker tests**

Assert the parse handler:

- Opens the stored artifact.
- Selects exactly one Parser.
- Stores parser profile and version.
- Builds a Batch hash from all files sorted by SourceFile ID.
- Calls Reconciler preview.
- Saves an immutable preview with 24 hour expiry.
- Marks unsupported headers with the exact received and required header sets.

Assert the apply handler:

- Revalidates preview hash.
- Loads the pre-created queued run and marks it running.
- Resumes from the stored checkpoint.
- Calls NetBox Writer.
- Saves ChangeSummary.
- Marks preview and source files applied.

Assert artifact expiry:

- A SourceFile with `expires_at <= now` is deleted from Artifact Store and its `artifact_status` is marked `expired` without changing its processing `status`.
- Metadata, hash, parser profile, and ChangeSummary remain queryable.
- A missing Artifact object is treated as an idempotent success.
- A SourceFile whose expiry is in the future is retained.

- [ ] **Step 2: Implement the Worker loop**

The Worker:

```python
while True:
    job = await queue.claim(worker_id)
    if job is None:
        await asyncio.sleep(1)
        continue
    await dispatch(job)
```

Handle SIGTERM by finishing the current transaction, releasing no uncommitted lock, and exiting. Do not log job payloads containing account metadata beyond provider, realm, and masked account ID.

Run `expire_artifacts` at Worker startup and at most once per hour. Set `expires_at` to upload time plus 30 days and never extend it on duplicate upload.

- [ ] **Step 3: Write failing UI tests**

Test:

- Upload form contains provider, realm, account, export type, exported time, and multiple file fields.
- Preview page displays summary counts and warnings.
- Apply form includes the hidden Batch hash.
- HTML escapes filenames, warnings, and resource names.
- Missing, invalid, mismatched, or older than two hours CSRF tokens return `403`.

- [ ] **Step 4: Implement server-rendered pages**

Use Jinja2 templates with no client framework. The UI calls the same service functions as JSON routes and provides links to NetBox after apply. Include a visible warning that only synthetic or approved files should be used in the public PoC.

Use `itsdangerous.URLSafeTimedSerializer` with `Settings.csrf_secret` and salt `cloud-inventory-csrf`. GET pages issue one signed random token in an `HttpOnly`, `SameSite=Strict`, path `/ui` cookie and render the same value in a hidden form field. POST handlers require constant-time equality, validate the signature with a two-hour maximum age, and rotate the token after a successful write. The local HTTP PoC leaves the cookie Secure flag off; operator documentation requires Secure cookies behind HTTPS before non-local deployment.

- [ ] **Step 5: Run and commit**

```bash
uv run pytest tests/unit/jobs/test_worker.py tests/api/test_ui.py -q
git add src/cloud_inventory/jobs/worker.py src/cloud_inventory/api src/cloud_inventory/app.py tests/unit/jobs/test_worker.py tests/api/test_ui.py
git commit -m "feat: process imports and render preview UI"
```

---

### Task 12: End-to-End PoC, CI, and Operator Documentation

**Files:**

- Create: `tests/integration/test_manual_import_flow.py`
- Create: `scripts/poc_import.sh`
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`
- Create: `docs/manual-import.md`
- Modify: `docs/superpowers/specs/2026-07-28-netbox-cloud-inventory-design.md`

**Interfaces:**

- Produces: reproducible local PoC command
- Produces: CI quality gate
- Produces: operator instructions for supported exports and safe test data

- [ ] **Step 1: Write the end-to-end test**

The test must:

1. Start the Compose dependencies.
2. Apply NetBox Portable Schema.
3. Create synthetic `platform` and `manual-override` Owners plus a `payments` BusinessService directly in NetBox.
4. Create TagMapping and OwnerMapping records for synthetic `Service=payments` and `Owner=platform` values.
5. Upload synthetic AWS Resource Explorer CSV.
6. Poll until preview is ready.
7. Approve with the returned Batch hash.
8. Poll until the run succeeds.
9. Query NetBox and assert the AWS objects, Owner, and BusinessService resource relation exist.
10. Repeat with NCP Server XLSX and the standard Bundle.
11. Repeat the existing apply requests and assert the same run IDs and no duplicate objects.
12. Replace one resource Owner with `manual-override`.
13. Upload a new synthetic Bundle with a later `exported_at`, the same resource identity, and the same `Owner=platform` hint; apply it and assert the manual Owner remains.
14. Upload another later Bundle that omits one prior resource and assert that resource does not become inactive.

- [ ] **Step 2: Add a one-command PoC script**

`scripts/poc_import.sh` must run:

```bash
docker compose up -d --build
uv run python scripts/apply_netbox_schema.py
uv run pytest tests/integration/test_manual_import_flow.py -q
```

Before Compose startup, the script uses `umask 077` and creates `.env` only when it does not exist. Generate independent hexadecimal values with `openssl rand` for:

```text
CONTROL_POSTGRES_PASSWORD: 32 bytes
NETBOX_POSTGRES_PASSWORD: 32 bytes
NETBOX_REDIS_PASSWORD: 32 bytes
NETBOX_REDIS_CACHE_PASSWORD: 32 bytes
NETBOX_SECRET_KEY: 64 bytes
NETBOX_API_TOKEN_PEPPER: 64 bytes
NETBOX_SUPERUSER_PASSWORD: 32 bytes
NETBOX_SUPERUSER_API_KEY: 6 bytes
NETBOX_SUPERUSER_API_TOKEN: 32 bytes
INVENTORY_CSRF_SECRET: 32 bytes
```

Derive `INVENTORY_DATABASE_URL` from the generated Control PostgreSQL password and `INVENTORY_NETBOX_TOKEN` as `nbt_<NETBOX_SUPERUSER_API_KEY>.<NETBOX_SUPERUSER_API_TOKEN>`. Write with mode `0600`, never print values, and never overwrite an existing `.env`. Exit non-zero on a missing `openssl`, health, schema, import, or assertion failure.

- [ ] **Step 3: Add CI**

The GitHub Actions workflow uses Python 3.12 and runs:

```bash
uv sync --locked --all-groups
uv run ruff check src tests scripts
uv run mypy src
uv run pytest tests/unit tests/api -q
uv run python scripts/export_import_schema.py --check
```

Do not run Docker integration tests in the initial required check. Add them as a manually triggered workflow job with a 20 minute timeout.

- [ ] **Step 4: Document supported manual sources**

`docs/manual-import.md` must include:

- AWS Resource Explorer CSV export steps and limitations.
- NCP Server, Public IP, Load Balancer, Object Storage XLSX download locations.
- Clear statement that NCP Resource Manager has no documented full-list Export.
- Import Bundle schema link and a synthetic example.
- Preview and approval flow.
- Duplicate file behavior.
- Manual omission safety.
- 30 day artifact retention.
- PoC local Artifact Volume has no application-level encryption and accepts only synthetic data or data approved for the host.
- Prohibition on committing real exports and credentials.

- [ ] **Step 5: Update README quickstart**

Include prerequisites, `uv sync`, Compose startup, NetBox schema application, UI URL, NetBox URL, and shutdown command. Mark the project as PoC and list the supported Parser profile IDs.

- [ ] **Step 6: Run the complete verification**

```bash
uv run ruff check src tests scripts
uv run mypy src
uv run pytest tests/unit tests/api -q
uv run python scripts/export_import_schema.py --check
docker compose config --quiet
./scripts/poc_import.sh
git diff --check
rg -n '\x{00B7}|\x{2022}' README.md docs src tests scripts || true
```

Expected:

- All tests pass.
- Compose configuration is valid.
- Schema check reports no difference.
- Repeated import creates no duplicate objects.
- Forbidden text scan prints no matches.

- [ ] **Step 7: Commit the completed PoC**

```bash
git add .github README.md docs scripts tests/integration
git commit -m "docs: complete manual import PoC workflow"
```

---

## PoC Acceptance Checklist

- [ ] AWS Resource Explorer CSV produces a preview and NetBox objects.
- [ ] NCP Server, Public IP, Load Balancer, and Bucket XLSX files produce previews and NetBox objects.
- [ ] Standard JSON Import Bundle represents all first-scope cloud resources, relationships, service hints, and owner hints.
- [ ] Unsupported or changed headers fail safely with actionable errors.
- [ ] Preview approval is bound to an immutable Batch hash.
- [ ] Same file and same Batch are idempotent.
- [ ] Manual omissions never delete or deactivate NetBox objects.
- [ ] NetBox user-managed fields remain unchanged.
- [ ] Worker resumes from the last completed write stage.
- [ ] Secrets and real infrastructure data are absent from source, logs, fixtures, and previews.
- [ ] Local Compose PoC can be reproduced from a clean checkout.

## Source References

- [FastAPI UploadFile](https://fastapi.tiangolo.com/reference/uploadfile/)
- [FastAPI Lifespan Testing](https://fastapi.tiangolo.com/advanced/testing-events/)
- [AWS Resource Explorer CSV Export](https://docs.aws.amazon.com/resource-explorer/latest/userguide/managing-resources.html)
- [NCP Resource Manager Resource Screen](https://guide.ncloud-docs.com/docs/en/resourcemanager-use-resource)
- [NCP Server List Download](https://guide.ncloud-docs.com/docs/en/server-screen-vpc)
- [NCP Public IP List Download](https://guide.ncloud-docs.com/docs/en/server-publicip-vpc)
- [NCP Load Balancer List Download](https://guide.ncloud-docs.com/docs/en/loadbalancer-screen-vpc)
- [NCP Object Storage Bucket List Download](https://guide.ncloud-docs.com/docs/en/objectstorage-use-screen)
- [NetBox REST API](https://netbox.readthedocs.io/en/stable/integrations/rest-api/)
- [NetBox 4.6 Release Notes](https://netbox.readthedocs.io/en/stable/release-notes/version-4.6/)
- [NetBox Docker](https://github.com/netbox-community/netbox-docker)
- [NetBox Custom Objects Installation](https://netboxlabs.com/docs/custom-objects/installation/)
- [NetBox Custom Objects Portable Schema](https://netboxlabs.com/docs/custom-objects/portable-schema/)
