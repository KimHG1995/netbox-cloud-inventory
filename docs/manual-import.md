# 수동 Import 운영 가이드

이 문서는 공급자 API를 연결하지 않고 AWS와 NAVER Cloud Platform 콘솔 Export 파일 또는 표준 JSON 파일을 NetBox Cloud Inventory에 반영하는 방법을 설명한다. 현재 구현은 운영 제품이 아닌 공개 PoC다. 합성 데이터 또는 이 PoC가 실행되는 호스트에 저장하도록 명시적으로 승인된 데이터만 사용한다.

## 로컬 환경 준비

개발 의존성을 설치하고 데모 데이터가 없는 로컬 환경을 실행한다.

```bash
uv sync --locked --all-groups
./scripts/start_local.sh
```

`start_local.sh`는 Docker Compose 기동, 상태 확인, NetBox 스키마 적용까지만 수행한다. 사용자가 내려받은 Export 파일만 확인하려면 `load_demo.sh`와 `test_integration.sh`를 실행하지 않는다.

환경이 준비되면 다음 주소를 사용한다.

- 수동 Import UI: `http://127.0.0.1:8080/ui/imports`
- NetBox: `http://127.0.0.1:8000`
- Inventory API 상태: `http://127.0.0.1:8080/healthz`

공개 합성 데이터로 먼저 동작을 확인하려면 별도로 실행한다.

```bash
./scripts/load_demo.sh
```

데모 적재기는 `examples/demo/`의 고정 CSV, XLSX, JSON 파일을 사용한다. 같은 데모를 반복 실행하면 기존 Import와 Run을 재사용한다.

## 지원 프로필

업로드 화면의 Export type에는 다음 프로필 ID 중 하나를 입력한다.

| 프로필 ID | 입력 파일 | 주요 리소스 |
|---|---|---|
| `aws.resource_explorer.csv.v1` | AWS Resource Explorer CSV | 계정, Region, 지원되는 리소스의 요약, 태그 |
| `ncp.server_list.xlsx.v1` | NCP Server 목록 XLSX | Region, Zone, Server, 사설 IP, 공인 IP |
| `ncp.public_ip_list.xlsx.v1` | NCP Public IP 목록 XLSX | Public IP와 적용 Server 관계 |
| `ncp.load_balancer_list.xlsx.v1` | NCP Load Balancer 목록 XLSX | Load Balancer 요약 |
| `ncp.object_storage_bucket_list.xlsx.v1` | NCP Object Storage Bucket 목록 XLSX | Bucket 요약 |
| `canonical.import_bundle.v1` | 표준 JSON Import Bundle | 1차 범위 전체 리소스와 관계 |

`auto`도 사용할 수 있지만, 운영자가 파일 출처를 알고 있다면 명시적인 프로필 ID를 권장한다. 명시한 프로필과 파일 헤더가 맞지 않으면 수신한 헤더와 필요한 헤더 이름을 오류에 함께 표시한다.

## AWS Resource Explorer CSV

AWS Resource Explorer에서 필요한 Query를 실행하고 결과 화면의 Export 기능으로 CSV를 내려받는다. 구체적인 화면 동작은 [AWS Resource Explorer 리소스 관리 문서](https://docs.aws.amazon.com/resource-explorer/latest/userguide/managing-resources.html)를 따른다.

업로드할 때 다음 값을 사용한다.

- Provider: `aws`
- Realm: 일반 AWS는 `commercial`, 별도 공공 환경을 모델링할 때는 해당 환경 정책에 맞는 Realm
- Account ID: CSV의 AWS account 값과 동일한 값
- Export type: `aws.resource_explorer.csv.v1`
- Region: CSV에 Region이 없는 전역 리소스만 있는 경우에도 계정의 기준 Region을 선택할 수 있음

필수 헤더는 `Identifier`, `Resource type`, `Region`, `AWS account`다. 대소문자와 공백 차이는 정규화한다. 나머지 열은 태그 후보로 읽으며 Access Key, Secret, Token처럼 민감한 이름의 열은 반영하지 않는다.

Resource Explorer CSV는 조회 결과의 요약 Export다. EC2 Instance가 포함되어도 Site 또는 Zone 관계를 만들 정보가 없으면 VM을 억지로 생성하지 않고 `unmaterializable_summary` 경고로 남긴다. VPC, Subnet, NIC, IP, DNS Record의 정확한 관계와 세부 속성이 필요하면 표준 Import Bundle을 사용한다.

## NAVER Cloud Platform XLSX

NCP는 현재 하나의 문서화된 전체 리소스 Export 파일이 아니라 서비스 화면별 목록 다운로드를 사용한다.

- Server 목록: [Server 화면 가이드](https://guide.ncloud-docs.com/docs/en/server-screen-vpc)
- Public IP 목록: [Public IP 화면 가이드](https://guide.ncloud-docs.com/docs/en/server-publicip-vpc)
- Load Balancer 목록: [Load Balancer 화면 가이드](https://guide.ncloud-docs.com/docs/en/loadbalancer-screen-vpc)
- Object Storage Bucket 목록: [Object Storage 화면 가이드](https://guide.ncloud-docs.com/docs/en/objectstorage-use-screen)

각 서비스 화면에서 목록을 XLSX로 내려받고 일치하는 프로필 ID를 선택한다. 한국어와 영어 헤더의 지원 이름은 Parser에 명시되어 있다. Region 열이 없는 Export는 업로드 화면의 Region 값이 필수다.

[NCP Resource Manager 리소스 화면 문서](https://guide.ncloud-docs.com/docs/en/resourcemanager-use-resource)에는 전체 목록을 파일로 내려받는 Export 절차가 문서화되어 있지 않다. 따라서 Resource Manager 화면을 전체 Export 원본으로 가정하지 않는다.

현재 NCP 서비스별 Parser는 목록 화면에 포함된 요약 정보만 반영한다. Cloud DB 세부 정보와 여러 서비스에 걸친 관계는 `canonical.import_bundle.v1`로 전달한다.

## 표준 JSON Import Bundle

공급자 Export가 표현하지 못하는 VPC, Subnet, VM, NIC, IP, Load Balancer, DNS, 관리형 Database, Object Storage와 관계를 하나의 부분 Snapshot으로 전달한다.

- JSON Schema: [schemas/import-bundle-v1.schema.json](../schemas/import-bundle-v1.schema.json)
- 전체 합성 예제: [examples/demo/full-inventory.json](../examples/demo/full-inventory.json)

최소 구조는 다음과 같다.

```json
{
  "schema_version": "1",
  "provider": "aws",
  "realm": "commercial",
  "account_id": "123456789012",
  "exported_at": "2026-07-28T00:00:00Z",
  "resources": [
    {
      "schema_version": "1",
      "uid": "aws:commercial:123456789012:global:object_bucket:example-bucket",
      "provider": "aws",
      "realm": "commercial",
      "account_id": "123456789012",
      "region": "global",
      "resource_type": "object_bucket",
      "external_id": "example-bucket",
      "name": "example-bucket",
      "observed_at": "2026-07-28T00:00:00Z"
    }
  ]
}
```

Bundle의 Provider, Realm, Account ID, exported_at은 업로드 요청 값과 일치해야 한다. 모든 `uid`는 동일한 정규 식별 규칙을 사용하며 관계 대상은 Bundle 안에 존재하거나 미해결 관계 경고로 기록된다. 수동 Bundle은 항상 부분 Snapshot으로 처리된다.

## Preview와 승인

브라우저에서 `http://127.0.0.1:8080/ui/imports`를 연다.

1. Provider, Realm, Account ID, Export type, Exported at, 선택적 Region과 파일을 입력한다.
2. 업로드 후 Preview가 준비될 때까지 결과 페이지를 새로 고침한다.
3. 생성, 변경, 유지, 경고, 오류와 리소스별 변경 필드를 확인한다.
4. Preview의 Batch hash가 유지된 상태에서 Apply를 누른다.
5. 실행 이력과 NetBox 객체를 확인한다.

JSON API를 사용하면 `POST /imports`, `GET /imports/{import_id}/preview`, `POST /imports/{import_id}/apply`, `GET /runs/{run_id}` 순서다. Apply 요청에는 Preview가 반환한 `batch_hash`를 그대로 넣어야 한다.

동일 Provider, Realm, Account ID와 동일 파일 내용은 기존 Import를 반환한다. 동일 Import와 Batch hash를 다시 Apply하면 기존 Run ID를 반환하며 객체를 중복 생성하지 않는다.

## 누락과 수동 보강의 안전성

모든 수동 파일은 부분 Snapshot이다. 새 파일에 기존 리소스가 없더라도 해당 객체를 삭제하거나 `inactive`로 바꾸지 않는다. 전체 목록처럼 보이는 파일도 이 규칙은 동일하다.

수집기가 관리하는 필드만 업데이트한다. NetBox에서 사용자가 입력한 설명, 운영 메모, 기존 BusinessService 관계는 제거하지 않는다. 승인된 OwnerMapping이 있더라도 객체에 다른 Owner가 수동 지정되어 있으면 해당 Owner를 보존하고 `owner_conflict` 경고를 남긴다.

## 보관과 보안

원본 파일의 기본 보존 기간은 업로드 시점부터 30일이다. Worker가 만료 파일을 Artifact Store에서 제거해도 Import, Preview, 실행 이력은 별도 제어 데이터로 유지된다.

로컬 PoC Artifact Store는 Docker 전용 Volume을 사용하지만 애플리케이션 수준 암호화를 제공하지 않는다. 운영 데이터는 디스크 암호화, 접근 제어, 백업, 삭제 정책이 준비된 승인 호스트에서만 시험한다.

다음 항목은 공개 저장소에 커밋하지 않는다.

- 실제 AWS 또는 NCP Export 파일
- 실제 Account ID, 내부 Domain, 내부 IP와 서비스 이름
- `.env`, Access Key, Secret Key, 임시 Token, NetBox Token
- NetBox Database Dump와 Artifact Volume 내용

저장소의 Fixture는 합성 데이터만 포함해야 한다. 실데이터가 한 번이라도 Git 이력에 들어갔다면 단순 삭제로 끝내지 말고 자격증명 폐기와 이력 정리 절차를 수행한다.
