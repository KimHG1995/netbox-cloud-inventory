# 로컬 실행과 데모 적재 분리 설계

## 배경

현재 `scripts/poc_import.sh`는 로컬 비밀 값 생성, Docker Compose 기동, 서비스 상태 확인, NetBox 스키마 적용, 합성 데이터 기반 통합 테스트를 한 번에 수행한다. 이 구조는 전체 PoC 검증에는 편리하지만 사용자가 실제 AWS와 NAVER Cloud Platform Export 파일을 직접 업로드하려는 경우에도 테스트 데이터가 함께 생성된다.

로컬 환경 실행, 사용자용 데모 적재, CI 통합 검증을 분리해 각 명령의 목적과 부작용을 명확하게 만든다.

## 목표

- 데모 데이터 없이 NetBox와 Inventory 서비스를 실행한다.
- 사용자가 실행 중인 환경에 실제 Export 파일을 UI로 업로드할 수 있게 한다.
- 공개 가능한 고정 합성 데이터를 명시적인 명령으로만 적재한다.
- 데모 적재를 테스트 코드와 분리하고 반복 실행 시 중복 객체를 만들지 않는다.
- 무작위 식별자를 사용하는 기존 통합 검증은 CI 전용 흐름으로 유지한다.
- 기존 `poc_import.sh` 사용자는 즉시 깨지지 않도록 호환 진입점을 제공한다.
- README와 GitHub About이 새 실행 흐름과 프로젝트 목적을 정확하게 설명하게 한다.

## 범위에서 제외하는 항목

- AWS와 NAVER Cloud Platform API 기반 자동 수집
- Kubernetes와 Amazon ECS 수집
- 운영 환경 배포 자동화
- 공개 데모 사이트 배포
- 실제 클라우드 Export 파일의 저장소 포함

## 스크립트 구조

### `scripts/lib/local_stack.sh`

로컬 실행 스크립트가 공유하는 Shell 함수를 제공한다.

- 필수 명령 존재 여부 확인
- Docker Compose v2 확인
- `.env`가 없을 때 비밀 값 생성
- 기존 `.env` 로드와 필수 환경 변수 검증
- URL 상태 확인과 제한 시간 처리

이 파일을 직접 실행하는 사용 흐름은 제공하지 않는다. `.env`는 기존과 같이 권한 `0600`으로 생성하며 이미 존재하는 파일은 덮어쓰지 않는다. 비밀 값은 표준 출력에 표시하지 않는다.

### `scripts/start_local.sh`

사용자가 실제 Export 파일을 직접 업로드하기 위한 기본 진입점이다.

1. 공통 로컬 환경 함수를 로드한다.
2. 필수 도구와 환경 변수를 검증한다.
3. `docker compose up -d --build`를 실행한다.
4. NetBox와 Inventory API가 준비될 때까지 기다린다.
5. `scripts/apply_netbox_schema.py`를 실행한다.
6. Import UI, NetBox, 상태 확인 주소를 출력한다.

데모 데이터 적재와 `pytest` 실행은 포함하지 않는다. 동일 명령을 반복 실행해도 기존 Docker Volume과 `.env`를 유지한다.

### `scripts/load_demo.sh`

이미 실행 중인 로컬 환경에만 데모 데이터를 적재하는 진입점이다.

1. 공통 함수를 사용해 `.env`와 필수 도구를 검증한다.
2. NetBox와 Inventory API 상태를 확인한다.
3. 서비스가 실행 중이 아니면 `scripts/start_local.sh`를 먼저 실행하라는 오류를 반환한다.
4. `uv run python scripts/load_demo_data.py`를 실행한다.
5. 반영된 Import와 Run 요약 및 조회 주소를 출력한다.

이 스크립트는 Docker Compose를 시작하거나 이미 저장된 데이터를 초기화하지 않는다.

### `scripts/load_demo_data.py`

테스트 함수에 의존하지 않는 사용자용 데모 적재기다. 저장소에 포함된 합성 입력을 Inventory API로 업로드하고 Preview를 확인한 뒤 Apply한다.

- AWS Resource Explorer CSV 예제
- NAVER Cloud Platform Server XLSX 예제
- VPC, Subnet, VM, IP, Database, Object Storage 관계를 포함한 표준 Import Bundle 예제

데모 계정 ID와 리소스 식별자는 고정한다. 동일 파일과 동일 계정 조합을 다시 실행하면 기존 Import와 Run을 재사용하는 현재 멱등성 규칙을 따른다. Preview에 오류가 있거나 Apply Run이 실패하면 0이 아닌 종료 코드를 반환한다.

사용자용 입력은 `examples/demo/` 아래에 둔다. CSV, XLSX, JSON 파일을 고정 Fixture로 저장해 반복 실행에서도 파일 해시가 변하지 않게 한다. 실제 Account ID, 내부 주소, 내부 서비스 이름은 포함하지 않는다.

### `scripts/test_integration.sh`

전체 로컬 통합 검증과 GitHub Actions 수동 Workflow의 진입점이다.

1. `scripts/start_local.sh`를 실행한다.
2. `RUN_MANUAL_IMPORT_E2E=1`을 설정한다.
3. 기존 `tests/integration/test_manual_import_flow.py`를 실행한다.

테스트는 충돌을 피하기 위해 기존처럼 무작위 계정과 리소스 식별자를 사용한다. 사용자용 데모 적재기와 테스트 Fixture는 서로 의존하지 않는다.

### `scripts/poc_import.sh`

기존 명령의 호환성을 위한 얇은 Wrapper로 유지한다. 사용 중단 안내와 대체 명령을 출력한 뒤 `scripts/test_integration.sh`를 실행한다. README의 기본 실행 경로에서는 사용하지 않는다.

## 사용자 흐름

### 실제 Export 파일 직접 업로드

```bash
uv sync --locked --all-groups
./scripts/start_local.sh
```

사용자는 `http://127.0.0.1:8080/ui/imports`에서 Provider, Realm, Account ID, Export type, Exported at, 선택적 Region과 파일을 입력한다. Preview의 생성, 변경, 경고, 오류를 검토한 후 Apply한다.

### 합성 데모 확인

```bash
./scripts/start_local.sh
./scripts/load_demo.sh
```

`load_demo.sh`는 고정 합성 데이터를 반영하며 반복 실행해도 중복 객체를 생성하지 않는다.

### 종료와 초기화

`docker compose down`은 컨테이너를 종료하고 이름이 지정된 Volume은 유지한다. `docker compose down -v`는 NetBox 데이터, Import 이력, Artifact를 삭제하므로 README에서 파괴적 초기화 명령으로 별도 경고한다.

## 오류 처리

- 필수 명령이 없으면 누락된 명령 이름과 함께 즉시 실패한다.
- Docker Compose v2를 사용할 수 없으면 즉시 실패한다.
- 기존 `.env`에 필수 값이 없으면 해당 환경 변수 이름을 표시하고 실패한다.
- 서비스 상태 확인은 최대 180초 동안 수행하고 제한 시간을 넘기면 대상 URL과 함께 실패한다.
- 데모 업로드, Preview, Apply 중 HTTP 오류가 발생하면 응답 상태와 안전하게 표시 가능한 오류 본문을 제공한다.
- 토큰, 비밀번호, 원본 파일 내용은 로그에 출력하지 않는다.

## 테스트와 검증

- Shell 스크립트는 `bash -n`으로 문법을 검사한다.
- 스크립트 계약 테스트는 `start_local.sh`가 테스트를 실행하지 않고 `load_demo.sh`가 Compose를 기동하지 않는 역할 경계를 확인한다.
- 데모 적재기 단위 테스트는 업로드, Preview 대기, Apply, Run 완료, 오류 종료를 가짜 HTTP 서버 응답으로 검증한다.
- 실제 Compose 검증은 `start_local.sh` 실행 후 데모를 두 번 적재해 두 번째 실행이 기존 Import와 Run을 재사용하는지 확인한다.
- 기존 단위, API, 타입 검사, Lint, Schema 일치 검사를 유지한다.
- GitHub Actions 수동 통합 Job은 `scripts/test_integration.sh`를 호출한다.

## 문서 변경

README의 빠른 시작은 `start_local.sh`를 기본 경로로 안내한다. 데모 적재, 실제 Export 업로드, 전체 통합 검증을 별도 절로 구분한다. 각 명령의 데이터 변경 여부와 반복 실행 효과를 설명한다.

수동 Import 운영 가이드에는 실행 중인 로컬 환경 준비 명령과 Import UI 진입점을 연결한다. 실제 Export 파일과 비밀 값은 저장소에 커밋하지 않는 기존 보안 지침을 유지한다.

## GitHub About

GitHub 저장소 About은 구현 완료 후 다음 기준으로 갱신한다.

- Description: `Manual AWS and NAVER Cloud inventory imports normalized into NetBox for centralized infrastructure discovery.`
- Topics: `netbox`, `aws`, `naver-cloud-platform`, `cloud-inventory`, `infrastructure-inventory`, `fastapi`, `python`, `docker`
- Website: 공개 배포 주소가 없으므로 설정하지 않는다.

## 호환성과 전환

기존 `scripts/poc_import.sh`는 제거하지 않는다. 새 이름을 안내하는 경고를 출력하되 기존 전체 PoC 검증 동작은 유지한다. 외부 사용자가 새 명령으로 전환할 시간을 확보한 뒤 다음 호환성 변경 시점에 제거 여부를 결정한다.
