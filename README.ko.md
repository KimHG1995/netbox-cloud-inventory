# NetBox Cloud Inventory

한국어 | [English](README.md)

문서 운영 원칙: 실행 절차와 기능 변경은 `README.md`와 `README.ko.md`를 같은 커밋에서 함께 수정합니다.

이 프로젝트는 AWS와 NAVER Cloud Platform 공공 및 민간 환경에 파편화된 인프라를 정리하고 조회할 수 있는지 검증하는 오픈소스 사이드 프로젝트입니다. 초기에는 합성 데이터와 테스트 계정으로 PoC를 만들고, 조회 효율과 운영 이점이 확인되면 사내 중앙 인프라 장부로 도입하는 것을 목표로 합니다.

클라우드 API 기반 자동 수집을 최종 기본 경로로 사용하고, 공급자 콘솔에서 내려받은 CSV와 XLSX 파일 및 표준 JSON Import Bundle의 수동 업로드를 제공합니다. 두 경로의 데이터는 같은 정규화와 검증 절차를 거쳐 NetBox에 반영됩니다.

현재 구현은 수동 파일 수집과 조회를 먼저 검증하는 PoC입니다. AWS와 NCP API Collector는 아직 구현하지 않았습니다.

## 빠른 시작

필수 도구는 다음과 같습니다.

- Docker Engine 또는 Docker Desktop과 Docker Compose v2
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- OpenSSL
- curl

개발 의존성을 잠금 파일 그대로 설치합니다.

```bash
uv sync --locked --all-groups
```

### 환경만 실행하고 실제 Export 업로드

```bash
./scripts/start_local.sh
```

`start_local.sh`는 다음 작업만 수행합니다.

- `.env`가 없을 때 로컬 비밀 값 생성
- 생성한 `.env` 권한을 `0600`으로 설정
- Docker Compose 이미지 빌드와 서비스 기동
- NetBox와 Inventory API 상태 확인
- NetBox Cloud Inventory 스키마 적용

기존 `.env`와 Docker Volume은 덮어쓰거나 초기화하지 않습니다. 데모 데이터와 테스트 데이터도 넣지 않으므로 AWS 또는 NCP에서 내려받은 파일을 직접 확인할 때 사용하는 기본 명령입니다.

서비스 주소는 다음과 같습니다.

- 수동 Import UI: `http://127.0.0.1:8080/ui/imports`
- Inventory API 상태: `http://127.0.0.1:8080/healthz`
- NetBox: `http://127.0.0.1:8000`

실제 Export 파일은 다음 순서로 반영합니다.

1. 수동 Import UI를 엽니다.
2. Provider, Realm, Account ID, Export type, Exported at과 선택적 Region을 입력합니다.
3. AWS CSV, NCP XLSX 또는 표준 JSON Import Bundle을 업로드합니다.
4. Preview에서 생성, 변경, 경고, 오류를 확인합니다.
5. Batch hash가 유지된 상태에서 Apply를 실행합니다.
6. NetBox에서 생성되거나 갱신된 객체와 관계를 조회합니다.

파일별 입력 규칙과 누락 안전성은 [수동 Import 운영 가이드](docs/manual-import.md)를 확인해야 합니다.

### 합성 데모 데이터 적재

환경을 먼저 실행한 다음 데모 데이터를 명시적으로 적재합니다.

```bash
./scripts/start_local.sh
./scripts/load_demo.sh
```

`load_demo.sh`는 Docker Compose를 시작하지 않습니다. 실행 중인 Inventory API와 NetBox의 상태를 확인한 후 `examples/demo/`의 고정 합성 데이터만 업로드하고 Preview와 Apply를 수행합니다.

- AWS Resource Explorer CSV
- NCP 공공 Server 목록 XLSX
- VPC, Subnet, VM, NIC, IP, Load Balancer, DNS, Database, Object Storage 관계를 포함한 표준 JSON Import Bundle

동일한 데모 파일을 반복 실행하면 기존 Import와 Run을 재사용하므로 중복 객체를 만들지 않습니다. 데모 실행 결과에는 각 Import ID, Run ID, 반영 요약이 표시됩니다.

### 전체 통합 검증

개발과 CI에서 전체 Compose 통합 흐름을 검증하려면 다음 명령을 사용합니다.

```bash
./scripts/test_integration.sh
```

이 명령은 `start_local.sh`를 실행한 다음 무작위 식별자의 합성 AWS, NCP, 표준 Bundle 데이터를 적재하고 멱등성, 수동 Owner 보존, 누락 안전성을 검사합니다. 사용자 파일만 확인하려는 경우에는 실행할 필요가 없습니다.

기존 `scripts/poc_import.sh`는 호환성을 위해 남아 있으며 `test_integration.sh`로 전달됩니다. 새 사용 흐름에서는 목적이 분명한 세 스크립트를 사용합니다.

### 종료와 초기화

다음 명령은 컨테이너를 종료하지만 이름이 지정된 Docker Volume과 저장 데이터는 유지합니다.

```bash
docker compose down
```

주의: 다음 명령은 NetBox 데이터, Import 이력, 업로드 Artifact를 포함한 Docker Volume을 삭제합니다. 완전 초기화가 필요한 로컬 PoC 환경에서만 실행합니다.

```bash
docker compose down -v
```

지원하는 Parser 프로필은 다음과 같습니다.

- `aws.resource_explorer.csv.v1`
- `ncp.server_list.xlsx.v1`
- `ncp.public_ip_list.xlsx.v1`
- `ncp.load_balancer_list.xlsx.v1`
- `ncp.object_storage_bucket_list.xlsx.v1`
- `canonical.import_bundle.v1`

실제 Export를 사용하기 전에 운영 가이드의 파일별 제한, Preview 승인, 누락 안전성, 보안 주의 사항을 확인해야 합니다.

## 목표

- 여러 클라우드 계정의 인프라를 하나의 NetBox에서 조회
- 공급자마다 다른 리소스 구조와 필드 이름을 공통 모델로 정규화
- 계정, VPC, Subnet, VM, NIC, IP, Load Balancer, DNS, Database, Object Storage 관계 추적
- 클라우드 태그를 활용한 업무 서비스와 담당 팀 연결
- API 수집 실패나 불완전한 Export 파일로 인한 데이터 유실 방지
- 동일 데이터의 반복 수집과 반복 업로드에 대한 멱등성 보장

## 1차 범위

### 지원 공급자와 환경

- AWS
- NAVER Cloud Platform 민간
- NAVER Cloud Platform 공공

### 수집 대상

- 클라우드 계정
- Region과 Zone
- VPC와 Subnet
- VM과 Network Interface
- 사설 IP와 공인 IP
- Load Balancer
- Domain, DNS Zone, DNS Record
- AWS RDS와 Aurora
- NAVER Cloud Platform Cloud DB for MySQL과 PostgreSQL
- AWS S3와 NAVER Cloud Platform Object Storage
- 클라우드 태그
- 태그에서 식별할 수 있는 업무 서비스와 담당 팀

### 후속 범위

- Kubernetes
- Amazon ECS
- Kubernetes Namespace, Service, Ingress, Workload
- 공급자별 추가 관리형 서비스

## 수집 방식

### 후속 자동 수집

스케줄러가 등록된 계정과 Region을 순회하며 공급자 API를 읽기 전용으로 호출합니다.

- AWS는 계정별 ReadOnly Role과 STS AssumeRole 사용
- NAVER Cloud Platform은 공공 및 민간 환경별 조회 전용 Sub Account 인증키 사용
- 계정별 기본 수집 주기는 6시간
- 필요할 때 즉시 실행 가능

### 수동 업로드

AWS와 NAVER Cloud Platform 콘솔에서 내려받은 파일 또는 이 프로젝트의 표준 Import Bundle을 업로드합니다.

- AWS Resource Explorer CSV 지원
- NAVER Cloud Platform 서비스 콘솔 XLSX 지원
- 전체 리소스와 관계를 표현하는 표준 JSON Import Bundle 지원
- 반영 전 생성, 변경, 오류 예상 결과 표시
- 동일 파일의 중복 반영 방지
- 파일에 없는 필드와 리소스는 삭제하지 않음
- 초기 등록, 망 분리, API 장애 상황에서 사용

## 처리 흐름

```text
자동 경로
Scheduler
  -> AWS Collector 또는 NCP Collector
  -> ResourceBatch

수동 경로
Export 파일 업로드
  -> 공급자별 File Parser
  -> ResourceBatch

공통 경로
ResourceBatch
  -> 검증
  -> 정규화
  -> 기존 데이터와 비교
  -> NetBox Upsert
  -> 실행 결과 기록
```

## 리소스 식별

동일 리소스는 다음 값을 조합한 `cloud_uid`로 식별합니다.

```text
provider
+ realm
+ account_id
+ region
+ resource_type
+ external_id
```

예시는 다음과 같습니다.

```text
aws:commercial:123456789012:ap-northeast-2:virtual_machine:i-012345
ncp:government:account-01:KR:virtual_machine:12345678
```

## NetBox 매핑

| 클라우드 리소스 | NetBox 대상 |
|---|---|
| 클라우드 계정 | CloudAccount Custom Object |
| Region | Region |
| Availability Zone | Site |
| VPC | VRF |
| Subnet | Prefix |
| VM | VirtualMachine |
| 모든 NIC | CloudNetworkInterface Custom Object |
| VM에 연결된 NIC | CloudNetworkInterface와 연결된 VMInterface |
| 사설 및 공인 IP | IPAddress |
| Load Balancer | CloudLoadBalancer Custom Object |
| 관리형 Database | ManagedDatabase Custom Object |
| Object Storage Bucket | ObjectBucket Custom Object |
| Domain과 DNS | Domain, DNSZone, DNSRecord Custom Object |
| 업무 서비스 | BusinessService Custom Object |
| 담당 팀 | NetBox Owner |

## 안전한 동기화 원칙

- API 호출 실패 시 기존 NetBox 데이터를 유지
- 서비스 또는 Region 단위로 성공 범위를 판정
- 완전하게 성공한 API 수집 범위에서만 미발견 계산
- 수동 업로드 파일의 누락으로는 미발견을 계산하지 않음
- 첫 번째 미발견은 `stale_candidate`로 표시
- 연속 3회 또는 7일 이상 미발견되면 `inactive`로 표시
- 수집기가 NetBox 객체를 자동으로 최종 삭제하지 않음
- Owner, BusinessService, 설명, 운영 메모와 같은 사용자 입력을 보존

## 구성 요소

- `inventory-api`: 계정 등록, 수동 업로드, 변경 미리보기, 실행 이력 조회
- `inventory-worker`: API 수집, 파일 파싱, 정규화, 비교, NetBox 반영
- `control-db`: 계정 설정, 작업 큐, 실행 상태, 파일 정보, 변경 요약 저장
- `artifact-store`: 업로드 원본을 보관하는 저장 인터페이스. PoC는 전용 로컬 볼륨을 사용하고 운영 환경에서는 S3 호환 저장소로 교체
- `netbox`: 정규화된 인프라 자산과 관계 저장 및 조회

초기 구현은 Python 3.12, FastAPI, PostgreSQL을 기준으로 합니다. 자산 전체를 별도 데이터베이스에 복제하지 않고, 제어 정보만 PostgreSQL에 저장합니다.

NetBox는 Owner 기능을 사용할 수 있는 4.5 이상을 요구하며, 초기 검증 기준 버전은 4.6입니다. 클라우드 고유 리소스는 NetBox Core 기능이 아니라 `netboxlabs-netbox-custom-objects` 플러그인의 Custom Object Type으로 표현합니다. 초기 호환 조합은 NetBox 4.6.x와 Custom Objects 0.6.x입니다.

수집 서비스는 NetBox 플러그인으로 구현하지 않습니다. Custom Object Type 스키마만 Portable Schema JSON으로 버전 관리하고, 수집 서비스는 NetBox와 Custom Objects REST API를 사용합니다.

## 보안 원칙

- 클라우드 수집 권한은 읽기 전용으로 제한
- 자격증명 값은 애플리케이션 데이터베이스에 저장하지 않음
- 계정 설정에는 Secret Manager의 `credential_ref`만 저장
- 로그에 Access Key, Secret Key, 임시 토큰을 기록하지 않음
- 수동 업로드는 권한을 가진 내부 사용자만 수행
- 업로드 파일의 크기, 확장자, 실제 콘텐츠 형식을 함께 검사
- 파일 하나의 최대 크기는 100 MB, 한 배치의 최대 파일 수는 20개로 제한
- 원본 업로드 파일의 기본 보존 기간은 30일

## 프로젝트 상태

현재 단계는 실제 운영 환경 사용을 전제로 하지 않는 수동 Import PoC입니다. FastAPI UI와 API, PostgreSQL 작업 큐, Worker, NetBox 4.6, Custom Objects Portable Schema, AWS CSV Parser, NCP XLSX Parser, 표준 JSON Parser와 멱등 Upsert를 구현했습니다.

Preview의 Batch hash 승인, 중복 업로드와 중복 Apply, 수동 Owner 보존, 수동 파일 누락 시 비활성화하지 않는 규칙을 자동 테스트합니다. GitHub Actions의 기본 품질 검사는 Docker 통합 테스트를 제외하고 실행하며, 전체 Compose 검증은 수동 실행 Workflow로 제공합니다.

PoC에서는 합성 Fixture와 별도 테스트 계정을 사용합니다. 사내 도입을 결정할 때 실제 계정 연결, SSO, Secret Manager, Backup, 접근 제어, 운영 책임자를 별도 운영 준비 단계에서 검증합니다.

## 설계문서

- [NetBox Cloud Inventory 설계문서](docs/superpowers/specs/2026-07-28-netbox-cloud-inventory-design.md)
- [수동 Import 운영 가이드](docs/manual-import.md)
- [한글과 영문 README 운영 설계](docs/superpowers/specs/2026-07-30-bilingual-readme-design.md)
- [표준 Import Bundle JSON Schema](schemas/import-bundle-v1.schema.json)

## 라이선스

이 프로젝트는 [Apache License 2.0](LICENSE)으로 공개합니다. 라이선스 조건에 따라 상업적 사용, 수정, 배포, 사적 사용이 가능합니다.

공개 저장소에는 실제 클라우드 Export 파일, 계정 ID, 내부 Domain, IP, 자격증명, NetBox Data를 커밋하지 않습니다. 예제와 테스트 Fixture는 합성 데이터만 사용합니다.
