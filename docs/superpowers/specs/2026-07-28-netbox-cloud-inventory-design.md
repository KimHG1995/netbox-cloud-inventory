# NetBox Cloud Inventory 설계

작성일: 2026-07-28
상태: 수동 Import 공개 PoC 구현

## 1. 배경

사내 인프라는 AWS와 NAVER Cloud Platform의 여러 계정 및 공공과 민간 환경에 분산되어 있다. 각 클라우드 콘솔은 해당 공급자와 계정 안에서는 상세 정보를 제공하지만, 다음과 같은 질문에 한 곳에서 답하기 어렵다.

- 특정 IP가 어느 계정, VPC, Subnet, VM에 연결되어 있는가
- 특정 Domain이 어느 Load Balancer, VM, Database, 업무 서비스로 이어지는가
- 특정 업무 서비스가 사용하는 인프라와 담당 팀은 무엇인가
- 서로 다른 계정에서 동일하거나 겹치는 CIDR을 사용하는가
- 수집이 실패했거나 오래된 데이터는 무엇인가

이 프로젝트는 NetBox를 중앙 인프라 장부로 사용하여 파편화된 정보를 정규화하고 조회할 수 있는지 검증하는 오픈소스 사이드 프로젝트다. 초기에는 합성 데이터와 테스트 계정으로 PoC를 만들고, 조회 효율과 운영 이점이 확인되면 사내 도입을 검토한다.

클라우드가 실행 상태의 원본이며 NetBox는 읽기 중심의 통합 System of Record로 동작한다.

## 2. 목표

- 실제 운영 계정을 연결하기 전에 합성 데이터와 테스트 계정으로 핵심 가설을 검증한다.
- AWS와 NAVER Cloud Platform 공공 및 민간 환경을 하나의 모델로 조회한다.
- API 기반 자동 수집과 공급자 Export 파일 기반 수동 업로드를 모두 지원한다.
- 자동과 수동 입력이 동일한 검증, 정규화, 비교, 반영 절차를 사용한다.
- 클라우드 공급자의 상세 필드를 잃지 않으면서 공통 관계를 표현한다.
- 반복 실행과 반복 업로드가 중복 객체를 만들지 않게 한다.
- 부분 실패나 불완전한 파일이 정상 데이터를 삭제하거나 비활성화하지 않게 한다.
- NetBox에서 사용자가 보강한 담당 팀, 업무 서비스, 설명, 운영 메모를 보존한다.
- 후속 Collector를 기존 정규화와 NetBox 반영 코드의 변경 없이 추가할 수 있게 한다.

## 3. 비목표

다음 항목은 1차 구현 범위에 포함하지 않는다.

- NetBox에서 클라우드 리소스를 생성하거나 변경하는 프로비저닝
- 비용 분석과 과금 최적화
- IAM 정책 분석과 보안 형상 진단
- 실시간 성능 메트릭과 로그 수집
- Kubernetes와 Amazon ECS 내부 워크로드 수집
- 수집기가 수행하는 NetBox 객체의 자동 최종 삭제
- NetBox를 권한 정보나 클라우드 자격증명의 저장소로 사용하는 것
- PoC 단계에서 운영 SLA와 고가용성을 보장하는 것

## 4. 설계 원칙

### 4.1 클라우드가 실행 상태의 원본이다

VM 상태, VPC, Subnet, IP, Endpoint, Database Engine과 같은 관측 필드는 최신의 유효한 클라우드 관측값을 따른다.

### 4.2 NetBox는 관계와 운영 메타데이터의 원본이다

BusinessService, Owner, 설명, 운영 메모와 같은 사용자 보강 정보는 클라우드 수집이 덮어쓰지 않는다.

### 4.3 입력 방식과 정규화를 분리한다

API Collector와 File Parser는 모두 같은 `ResourceBatch`를 생성한다. 이후 검증, 정규화, 비교, NetBox 반영은 입력 방식과 무관하게 같은 코드를 사용한다.

### 4.4 불완전한 관측은 삭제 근거가 아니다

API 수집 범위가 완전하게 성공한 경우에만 미발견을 계산한다. 수동 Export 파일은 명시적으로 전체 스냅샷임을 입증하지 않는 한 부분 데이터로 취급한다.

### 4.5 공급자 원본과 공통 모델을 함께 보존한다

공통 조회에 필요한 값은 정규화된 필드로 저장한다. 공급자 고유 정보는 제한된 JSON 메타데이터나 원본 파일 참조로 보존한다.

## 5. 범위

### 5.1 공급자

- AWS
- NAVER Cloud Platform 민간
- NAVER Cloud Platform 공공

### 5.2 1차 수집 리소스

- 클라우드 계정
- Region
- Zone
- VPC
- Subnet
- VM
- Network Interface
- 사설 IP
- 공인 IP
- Load Balancer
- Domain
- DNS Zone
- DNS Record
- AWS RDS와 Aurora
- NAVER Cloud Platform Cloud DB for MySQL
- NAVER Cloud Platform Cloud DB for PostgreSQL
- AWS S3 Bucket
- NAVER Cloud Platform Object Storage Bucket
- 클라우드 태그
- 태그에서 식별되는 BusinessService와 Owner

### 5.3 후속 확장

- Kubernetes Cluster와 하위 리소스
- Amazon ECS Cluster와 하위 리소스
- NAVER Cloud Platform Cloud DB for MSSQL, Redis, MongoDB
- 공급자별 추가 관리형 서비스

후속 리소스는 `Collector`, `FileParser`, `Normalizer` 인터페이스를 구현하는 방식으로 추가한다.

## 6. 전체 아키텍처

NetBox는 4.5 이상을 요구하며 초기 검증 기준 버전은 4.6이다. Owner 기능은 NetBox Core를 사용하고, 클라우드 고유 리소스는 `netboxlabs-netbox-custom-objects` 플러그인의 Custom Object Type으로 정의한다.

초기 지원 조합은 NetBox 4.6.x와 Custom Objects 0.6.x다. Custom Objects 0.6.x는 NetBox 4.5.2부터 4.6.x를 지원하며 Custom Object의 Owner와 Portable Schema를 제공한다. 수집 로직 자체는 NetBox 플러그인으로 구현하지 않고 독립 서비스로 유지한다.

```text
자동 입력
Scheduler
  -> Account Registry
  -> PostgreSQL Job Queue
  -> AWS Collector 또는 NCP Collector
  -> ResourceBatch

수동 입력
Upload API
  -> File Validator
  -> Artifact Store
  -> AWS 또는 NCP File Parser
  -> ResourceBatch

공통 처리
ResourceBatch
  -> Schema Validator
  -> Resource Normalizer
  -> Reconciler
  -> NetBox Writer
  -> CollectionRun Result

운영 의존성
Secret Manager
  -> inventory-worker

Artifact Store
  -> inventory-api
  -> inventory-worker

NetBox
  <- NetBox Writer
```

### 6.1 inventory-api

내부 운영자를 위한 제어 API와 최소 관리 화면을 제공한다.

- 계정 등록과 상태 조회
- 자동 수집 즉시 실행
- 계정별 스케줄 설정
- Export 파일 업로드
- 반영 전 변경 미리보기
- 실행 이력과 오류 조회
- TagMapping과 OwnerMapping 관리

초기 화면은 FastAPI의 서버 렌더링 방식으로 구성한다. 자산 검색과 관계 조회는 NetBox UI를 사용하므로 별도 자산 조회 화면을 중복 개발하지 않는다.

### 6.2 inventory-worker

수집과 반영을 수행한다.

- AWS와 NAVER Cloud Platform API 호출
- 공급자 Export 파일 파싱
- 공통 스키마 검증
- 리소스 정규화
- 기존 NetBox 객체와 비교
- 멱등 Upsert
- 실행 결과와 오류 기록
- PostgreSQL 작업 큐에서 실행할 작업 선점
- 중단된 작업의 안전한 재개

### 6.3 control-db

PostgreSQL을 사용하며 인프라 자산 전체를 저장하지 않는다.

- AccountConfig
- CollectionSchedule
- CollectionJob
- CollectionRun
- CollectionScopeResult
- SourceFile
- ImportPreview
- ChangeSummary
- CredentialReference
- TagMapping
- OwnerMapping

실제 정규화된 자산과 관계는 NetBox에 저장한다.

작업 큐는 PostgreSQL의 행 잠금과 `SKIP LOCKED`를 사용한다. 별도 Redis를 도입하지 않으며, 여러 Worker가 같은 작업을 실행하지 않도록 `CollectionJob`을 원자적으로 선점한다.

### 6.4 artifact-store

수동 업로드 원본과 선택적으로 활성화된 API 진단 응답을 저장하는 S3 호환 저장소다.

수동 Import PoC에서는 외부 Object Storage를 추가하지 않고 inventory-api와 inventory-worker가 공유하는 접근 제한 로컬 볼륨을 사용한다. 저장 인터페이스를 분리하여 운영 전환 시 S3 호환 저장소로 교체한다.

- PoC 로컬 볼륨에는 합성 데이터나 반입 승인을 받은 자료만 30일 보관한다.
- 운영 S3 호환 저장소는 Server Side Encryption을 강제하고 원본을 30일 보관한다.
- API 원본 응답 저장은 기본적으로 비활성화한다.
- 장애 분석을 위해 API 원본 저장을 활성화한 경우 7일 후 삭제한다.
- control-db에는 객체 Key, 해시, 크기, MIME 유형, 만료 시각만 저장한다.
- 운영자가 승인한 Worker와 API만 저장소에 접근할 수 있다.

### 6.5 NetBox

다음 역할을 담당한다.

- 정규화된 리소스 저장
- 리소스 관계 탐색
- 검색과 필터
- Owner와 BusinessService 보강
- 변경 이력
- 사용자 권한

NetBox 배포에는 Custom Objects 플러그인과 해당 플러그인의 Background Job에 필요한 Redis가 포함된다. 이는 수집 서비스의 작업 큐와 분리된 NetBox 내부 의존성이다.

### 6.6 Secret Manager

AWS Role ARN, External ID, NAVER Cloud Platform Access Key와 Secret Key, NetBox API Token을 보관한다. 애플리케이션에는 Secret 값을 전달하지 않고 실행 시점에 `credential_ref`를 해석해 필요한 프로세스 메모리에만 적재한다.

## 7. 계정 모델

계정 설정은 다음 필드를 가진다.

```text
id
provider
realm
account_id
display_name
collection_mode
credential_ref
enabled_regions
schedule
enabled
```

`provider` 값은 `aws` 또는 `ncp`이다.

`realm`은 API Endpoint와 자격증명 영역을 구분한다. 초기 값은 `commercial`과 `government`이다.

`collection_mode`는 `api`, `manual`, `hybrid` 중 하나다.

- `api`: 자동 API 수집만 사용
- `manual`: 수동 Export 파일만 사용
- `hybrid`: 자동 API 수집을 기본으로 하고 수동 업로드도 허용

기본값은 `hybrid`다.

## 8. 자동 수집

### 8.1 AWS

- 계정별 ReadOnly Role을 등록한다.
- Worker는 STS AssumeRole로 임시 자격증명을 발급받는다.
- 활성 Region을 순회한다.
- 리소스 종류별 AWS API를 호출한다.
- Pagination을 모두 소진할 때까지 조회한다.

장기 Access Key를 계정별로 저장하지 않는다. 공급자 계정이 같은 AWS Organization에 속하지 않더라도 계정별 Role ARN을 등록할 수 있다.

### 8.2 NAVER Cloud Platform

- 공공과 민간 환경별 조회 전용 Sub Account 인증키를 사용한다.
- 공공과 민간 API Endpoint를 `realm` 설정으로 선택한다.
- Region과 서비스별 목록 API를 호출한다.
- Object Storage는 해당 환경의 S3 호환 Endpoint를 사용한다.

NAVER Cloud Platform 인증키는 Secret Manager에서 실행 시 읽으며 애플리케이션 DB에는 저장하지 않는다.

### 8.3 스케줄

- 계정별 기본 주기는 6시간이다.
- 계정별로 주기를 변경할 수 있다.
- 사용자는 즉시 실행을 요청할 수 있다.
- 같은 계정의 수집과 업로드는 동시에 적용하지 않는다.

### 8.4 AWS 수집 범위

| 리소스 | 주요 조회 API | 완전성 기준 |
|---|---|---|
| 계정 | STS GetCallerIdentity | 계정 식별 성공 |
| Region과 Zone | EC2 DescribeRegions, DescribeAvailabilityZones | 활성 Region 전체 조회 |
| VPC와 Subnet | EC2 DescribeVpcs, DescribeSubnets | 모든 Page 완료 |
| VM | EC2 DescribeInstances | 모든 Page 완료 |
| NIC와 IP | EC2 DescribeNetworkInterfaces, DescribeAddresses | 모든 Page 완료 |
| Load Balancer | ELBv2 DescribeLoadBalancers, DescribeListeners | Region 내 모든 Page 완료 |
| Backend | ELBv2 DescribeTargetGroups, DescribeTargetHealth | 모든 Target Group 조회 |
| DNS Zone과 Record | Route 53 ListHostedZones, ListResourceRecordSets | 모든 Zone과 Page 완료 |
| 등록 Domain | Route 53 Domains ListDomains | 권한이 있는 계정에서 모든 Page 완료 |
| Database | RDS DescribeDBInstances, DescribeDBClusters | 모든 Page 완료 |
| Object Storage | S3 ListBuckets와 Bucket별 상세 조회 | 모든 Bucket의 Region과 설정 조회 |
| 태그 | 서비스별 태그 API와 Resource Groups Tagging API | 대상 리소스의 태그 조회 완료 |

S3 Bucket은 전역 목록을 먼저 조회한 후 각 Bucket의 Region, Versioning, Encryption, Public Access Block, Tag를 개별 조회한다. 개별 Bucket의 접근이 거부되면 Bucket 객체는 유지하고 상세 완전성을 `partial`로 기록한다.

### 8.5 NAVER Cloud Platform 수집 범위

| 리소스 | 주요 조회 API | 완전성 기준 |
|---|---|---|
| Region과 Zone | getRegionList, getZoneList | 조회 가능한 Region과 Zone 전체 완료 |
| VPC와 Subnet | getVpcList, getSubnetList | 모든 Page 완료 |
| VM | getServerInstanceList | Region별 모든 Page 완료 |
| NIC와 IP | Network Interface 목록, getPublicIpInstanceList | Region별 모든 Page 완료 |
| Load Balancer | getLoadBalancerInstanceList, getLoadBalancerListenerList | Region별 모든 Page 완료 |
| Backend | getTargetGroupList, getTargetList | 모든 Target Group 조회 |
| DNS Zone과 Record | Global DNS Domain 목록과 Record 목록 | 모든 Domain과 Page 완료 |
| Database | getCloudMysqlInstanceList, getCloudPostgresqlInstanceList | Region별 모든 Page 완료 |
| Object Storage | S3 호환 ListBuckets와 Bucket별 상세 조회 | Realm별 모든 Bucket 조회 |
| 태그 | Resource Manager와 서비스별 태그 조회 | 대상 리소스의 태그 조회 완료 |

NAVER Cloud Platform 민간과 공공은 같은 정규화 모델을 사용한다. API Host, Object Storage Endpoint, Global DNS Endpoint, Credential은 `realm`에 따라 선택하며 서로 교차 사용하지 않는다.

### 8.6 호출 제어

- 모든 목록 API는 Pagination을 강제한다.
- 공급자와 서비스별 동시 호출 수를 제한한다.
- Retry-After가 있으면 해당 값을 우선 사용한다.
- Retry-After가 없으면 지수 백오프와 무작위 지연을 사용한다.
- 계정과 Region별 요청 수, 지연 시간, Rate Limit 횟수를 기록한다.
- API Endpoint는 코드에 등록된 공급자 Host만 허용하고 사용자 임의 URL을 호출하지 않는다.

## 9. 수동 Export 파일 업로드

### 9.1 지원 형식

- AWS Resource Explorer CSV
- NAVER Cloud Platform 서비스 콘솔 XLSX
- 프로젝트 표준 JSON Import Bundle

지원되는 공급자 Export 형식은 Parser 버전으로 관리한다. 파일 형식이 알려진 버전과 일치하지 않으면 적용하지 않고 오류와 필요한 열을 표시한다.

파일 하나의 최대 크기는 100 MB다. 한 업로드 배치에는 최대 20개 파일을 포함할 수 있다.

### 9.2 업로드 메타데이터

파일 자체에 다음 값이 없으면 사용자가 업로드 시 선택한다.

- provider
- realm
- account_id
- region
- resource_type
- exported_at

### 9.3 처리 단계

```text
업로드
-> 파일 해시 계산
-> 크기와 콘텐츠 형식 검사
-> 공급자와 파일 유형 판별
-> 필수 열과 값 검증
-> ResourceBatch 변환
-> 생성, 변경, 오류 미리보기
-> 사용자 승인
-> NetBox 반영
```

파일 해시, Parser 버전, Export 기준 시각을 SourceFile에 저장한다. 동일 파일을 다시 올리면 기존 처리 결과를 반환하고 중복 반영하지 않는다.

### 9.4 불완전성

공급자 콘솔의 Export 파일은 API 응답보다 적은 필드를 제공할 수 있다. Parser는 없는 값을 추정하지 않는다. 값이 없으면 `unknown`으로 표시하거나 기존 값을 보존한다.

수동 Export 파일의 리소스 누락은 삭제나 비활성화 근거로 사용하지 않는다.

### 9.5 Parser Profile

각 파일은 공급자, Export 화면, Resource Type, Header Signature로 식별되는 Parser Profile을 사용한다.

```text
ParserProfile
- provider
- realm
- export_type
- resource_type
- schema_version
- required_columns
- optional_columns
- header_fingerprint
```

초기 Parser는 다음 범위를 우선 지원한다.

- AWS Resource Explorer CSV
- NAVER Cloud Platform Server 목록 XLSX
- NAVER Cloud Platform Public IP 목록 XLSX
- NAVER Cloud Platform Load Balancer 목록 XLSX
- NAVER Cloud Platform Object Storage Bucket 목록 XLSX
- 전체 1차 리소스와 관계를 표현하는 표준 JSON Import Bundle

AWS Resource Explorer CSV는 Identifier, Resource Type, Region, AWS Account, Tag를 제공하는 요약 입력으로 취급한다. 상세 Network 관계와 Database 속성은 표준 JSON Import Bundle 또는 후속 API Collector가 제공한다.

NAVER Cloud Platform Resource Manager의 공식 화면에는 전체 리소스 조회와 검색은 있지만 파일 다운로드 기능이 문서화되어 있지 않으므로 Resource Manager Export를 Parser 입력으로 가정하지 않는다. NCP는 공식적으로 다운로드가 확인된 서비스별 XLSX를 사용하고, 다운로드가 없는 VPC, Subnet, Cloud DB, DNS 상세 정보는 표준 JSON Import Bundle로 입력한다.

공급자 Export 화면이 제공하지 않는 상세 필드는 수동 업로드만으로 생성하지 않는다. 요약 Export는 리소스 존재와 태그를 보강하고, 상세 Export가 있는 경우에만 네트워크 관계와 서비스 속성을 갱신한다.

### 9.6 미리보기 불변성

미리보기 생성 시 정규화된 ResourceBatch의 해시를 계산한다. 사용자가 적용을 승인할 때 같은 해시의 Batch만 반영한다.

- 미리보기 이후 파일이나 Parser 버전이 바뀌면 승인을 무효화한다.
- 미리보기는 생성, 갱신, 관계 변경, 보존, 경고, 오류 건수를 구분한다.
- 적용 전에 오류가 있는 행은 기본적으로 전체 Batch를 막는다.
- 사용자가 명시적으로 `유효한 행만 적용`을 선택하면 오류 행을 제외하고 실행 상태를 `partial`로 기록한다.
- 미리보기는 24시간 후 만료되며 만료된 미리보기는 다시 생성해야 한다.

## 10. 공통 ResourceBatch

```text
ResourceBatch
- schema_version
- batch_id
- run_id
- provider
- realm
- account_id
- source
- observed_at
- completeness
- scopes
- resources
- warnings
- content_hash
```

`source`는 `api` 또는 `export`다.

`completeness`는 `full` 또는 `partial`이다.

`scopes`는 다음 조합의 목록이다.

```text
provider
+ realm
+ account_id
+ region
+ resource_type
```

API Collector가 특정 Scope의 Pagination을 모두 완료했을 때 해당 Scope만 `full`로 표시한다. 오류가 발생했거나 파일의 전체성이 확인되지 않으면 `partial`로 표시한다.

## 11. 공통 CloudResource

```text
CloudResource
- schema_version
- uid
- provider
- realm
- account_id
- region
- zone
- resource_type
- external_id
- name
- status
- tags
- attributes
- relationships
- observed_at
- source
- completeness
- detail_level
- raw_reference
- fingerprint
```

`detail_level`은 공급자 목록 Export의 `summary`와 상세 API 또는 표준 Import Bundle의 `detailed`를 구분한다. 이는 Scope 전체성을 나타내는 `completeness`와 별개다.

### 11.1 식별자

`uid`는 다음 조합으로 생성한다.

```text
{provider}:{realm}:{account_id}:{region}:{resource_type}:{external_id}
```

Region이 없는 전역 리소스는 Region 자리에 `global`을 사용한다.

### 11.2 관계

관계는 다음 형식으로 정규화한다.

```text
Relationship
- type
- source_uid
- target_uid
```

초기 관계 유형은 다음과 같다.

- contains
- attached_to
- uses_subnet
- assigned_ip
- resolves_to
- fronts
- routes_to
- serves
- owned_by

### 11.3 공통 상태

공급자 상태는 다음 공통 상태로 정규화한다.

| 공통 상태 | 의미 |
|---|---|
| provisioning | 생성 또는 설정 진행 중 |
| active | 정상 사용 가능 |
| stopped | 정상적으로 중지됨 |
| degraded | 일부 기능만 동작하거나 복구 진행 중 |
| failed | 생성 또는 운영 실패 |
| deleting | 삭제 진행 중 |
| inactive | 미발견 정책에 의해 비활성화됨 |
| unknown | 공급자 상태를 해석할 수 없음 |

공급자 원본 상태는 `attributes.source_status`에 항상 보존한다. 알 수 없는 새 상태는 `unknown`으로 매핑하고 경고를 기록하며, 자동으로 `inactive`나 `failed`로 간주하지 않는다.

### 11.4 변경 Fingerprint

리소스의 클라우드 관리 필드와 관계를 정렬한 뒤 안정적인 Fingerprint를 계산한다.

- Fingerprint가 같으면 NetBox PATCH를 생략한다.
- 사용자 관리 필드는 Fingerprint에 포함하지 않는다.
- 태그 순서, JSON Key 순서, 공급자 응답 순서는 결과에 영향을 주지 않는다.
- Schema Version이 바뀌면 Fingerprint를 다시 계산한다.

## 12. NetBox 매핑

이 문서의 Custom Object는 NetBox Core의 Custom Field가 아니라 Custom Objects 플러그인의 Custom Object Type을 의미한다. Type 정의는 저장소의 Portable Schema JSON을 단일 원본으로 관리한다.

- 개발 환경에서 스키마를 변경하고 Portable Schema를 Export한다.
- 변경된 JSON은 코드 검토를 거쳐 Version Control에 반영한다.
- 배포 전에 `/api/plugins/custom-objects/schema/preview/`로 차이를 검증한다.
- 검증된 스키마만 Staging과 Production 순서로 적용한다.
- 필드 제거와 같은 파괴적 변경은 기본 차단하며 별도 승인 없이는 적용하지 않는다.
- 수집 데이터는 `/api/plugins/custom-objects/<slug>/` REST API로 Upsert한다.

| 공통 리소스 | NetBox 모델 |
|---|---|
| CloudAccount | CloudAccount Custom Object |
| Region | Region |
| Zone | Site |
| VPC | VRF |
| Subnet | Prefix |
| VirtualMachine | VirtualMachine |
| 모든 NetworkInterface | CloudNetworkInterface Custom Object |
| VM에 연결된 NetworkInterface | CloudNetworkInterface와 연결된 VMInterface |
| IPAddress | IPAddress |
| LoadBalancer | CloudLoadBalancer Custom Object |
| ManagedDatabase | ManagedDatabase Custom Object |
| ObjectBucket | ObjectBucket Custom Object |
| Domain | Domain Custom Object |
| DNSZone | DNSZone Custom Object |
| DNSRecord | DNSRecord Custom Object |
| BusinessService | BusinessService Custom Object |
| 담당 팀 | Owner |

DNS는 조회 목적으로만 저장하므로 1차 구현에서는 NetBox DNS 플러그인을 사용하지 않는다. NetBox를 DNS 설정의 원본으로 전환하는 요구가 생기면 별도 설계로 검토한다.

### 12.1 공통 Custom Field

수집 대상 NetBox 객체에는 다음 필드를 적용한다.

```text
cloud_uid
provider
realm
account_id
external_id
collection_source
cloud_status
last_seen_at
sync_state
source_tags
source_attributes
```

`cloud_uid`는 유일 식별자로 사용한다.

### 12.2 CloudAccount

```text
name
provider
realm
account_id
status
collection_mode
console_url
last_success_at
last_run_status
```

`provider + realm + account_id` 조합은 유일해야 한다. CloudAccount는 모든 클라우드 Custom Object가 참조하는 최상위 객체다. NetBox Core 객체는 동적 Custom Object를 직접 참조하지 않고 provider, realm, account_id Custom Field 조합으로 같은 계정에 귀속된다.

### 12.3 CloudNetworkInterface

분리되어 VMInterface로 표현할 수 없는 NIC를 저장한다.

```text
cloud_uid
external_id
cloud_account
region
zone
vpc
subnet
mac_address
private_ips
public_ips
attachment_status
attached_virtual_machine
source_tags
```

NIC가 VM에 연결되면 VMInterface를 생성하고 CloudNetworkInterface를 자동 삭제하지 않는다. 대신 `attachment_status = attached`와 VMInterface 참조를 설정하여 분리와 재연결 이력을 유지한다.

### 12.4 CloudLoadBalancer

```text
cloud_uid
external_id
cloud_account
name
load_balancer_type
scheme
status
vpc
subnets
dns_name
frontend_ips
listeners
target_groups
backend_resources
source_tags
```

Listener와 Target Group은 1차 구현에서 별도 Custom Object로 만들지 않고 구조화된 JSON과 리소스 참조로 저장한다. Listener 또는 Target Group을 독립적으로 소유하고 검색해야 하는 요구가 생기면 후속 Schema Version에서 분리한다.

### 12.5 ManagedDatabase

```text
cloud_uid
external_id
cloud_account
name
engine
engine_version
topology
status
vpc
subnets
endpoint
port
public_access
high_availability
multi_zone
encrypted
backup_enabled
source_tags
```

Database Credential, 사용자 목록, 연결 문자열의 비밀번호는 수집하지 않는다.

### 12.6 ObjectBucket

```text
cloud_uid
external_id
cloud_account
name
region
status
versioning
encryption
public_access
object_lock
source_tags
console_url
```

Object 목록과 Object 내용은 수집하지 않는다. Bucket 단위 메타데이터만 관리한다.

### 12.7 Domain

```text
cloud_uid
external_id
cloud_account
name
registrar
status
registered_at
expires_at
auto_renew
name_servers
business_service
owner_hint
```

Domain 등록 정보를 공급자 API나 Export가 제공하지 않으면 DNSZone만 생성하고 Domain을 추정 생성하지 않는다.

### 12.8 DNSZone과 DNSRecord

```text
DNSZone
- cloud_uid
- external_id
- cloud_account
- name
- visibility
- vpc_links
- name_servers
- record_count

DNSRecord
- cloud_uid
- external_id
- zone
- name
- record_type
- values
- ttl
- alias_target
- related_resources
```

A와 AAAA는 IPAddress에 연결한다. CNAME과 Alias는 대상 DNSRecord 또는 CloudLoadBalancer에 연결한다. 대상을 찾지 못하면 문자열 값을 유지하고 `unresolved_relation` 경고를 기록한다.

### 12.9 BusinessService

```text
name
service_code
environment
criticality
status
owner
runbook_url
repository_url
resources
```

`owner`는 여러 사용자와 그룹을 묶을 수 있는 NetBox 기본 Owner 필드다. BusinessService는 태그 값만으로 무조건 생성하지 않는다. 승인된 TagMapping이 고유한 `service_code`로 기존 BusinessService를 찾을 때만 `resources` 관계에 추가한다. 수동으로 연결된 리소스는 수집기가 제거하지 않는다.

### 12.10 Core Object 사용 규칙

- Region 이름은 `{provider}-{realm}-{region_code}` 형식을 사용한다.
- Site 이름은 `{provider}-{realm}-{zone_code}` 형식을 사용한다.
- VRF 이름은 `{provider}-{account_id}-{vpc_id}` 형식을 사용한다.
- VirtualMachine 이름이 계정 안에서 중복될 수 있으므로 표시 이름과 `cloud_uid`를 분리한다.
- Prefix는 VPC에 대응하는 VRF 안에 생성한다.
- IPAddress는 가능한 경우 VMInterface에 할당한다.
- Load Balancer와 Database Endpoint의 IP가 공급자에서 제공되지 않으면 DNS 이름만 저장하고 DNS 조회로 IP를 추정하지 않는다.

## 13. 태그 정규화

공급자 원본 태그는 그대로 보존한다. 공통 의미를 가진 키만 별도 정규화 필드로 투영한다.

```text
Service, service, application, app
-> BusinessService

Owner, owner, team, managed-by
-> Owner

Environment, environment, env, stage
-> environment
```

키 비교는 대소문자를 구분하지 않지만 원본 키와 값은 변경하지 않는다. 매핑은 provider, realm, account_id 범위로 분리한다. 둘 이상의 키가 충돌하면 계정별 TagMapping 설정의 우선순위를 사용하고 경고를 기록한다.

태그의 Owner 값은 `owner_hint`로 먼저 저장한다. OwnerMapping이 기존 NetBox Owner를 가리키는 경우에만 연결하며, 수집기가 NetBox 사용자나 Owner를 자동 생성하지 않는다. 매핑되지 않은 값은 운영 화면의 `미해결 담당자` 목록에 표시한다.

BusinessService 태그도 같은 방식으로 `service_hint`를 저장한다. TagMapping이 승인된 BusinessService를 가리킬 때만 자동 연결한다. 이를 통해 `payment`, `payments`, `PAYMENT`와 같은 변형이 중복 서비스를 만드는 것을 방지한다.

## 14. 병합과 필드 소유권

### 14.1 클라우드 관측 필드

다음 필드는 가장 최신의 유효한 관측값으로 갱신한다.

- status
- spec
- IP
- Endpoint
- Region
- Zone
- 공급자 원본 태그

API와 Export가 같은 필드를 제공하면 `observed_at`이 더 최신인 값을 사용한다. `unknown`, 빈 열, 파싱 실패 값은 기존 값을 지우지 않는다.

API의 `observed_at`은 Scope 조회가 완료된 시각을 사용한다. Export는 공급자가 기록한 Export 시각을 우선 사용한다. Export 시각이 없거나 신뢰할 수 없으면 업로드 시각을 기록하되, 해당 파일은 기존 API 관측값을 덮어쓰지 않고 빈 필드만 보강한다.

현재 시각보다 5분 이상 미래인 관측 시각은 거부한다. 계정과 Worker의 시계 차이는 운영 지표로 기록한다.

### 14.2 사용자 관리 필드

다음 필드는 수집기가 변경하지 않는다.

- BusinessService에 대한 수동 연결
- Owner에 대한 수동 연결
- 설명
- 운영 메모
- NetBox 사용자가 추가한 태그

### 14.3 관계

완전한 API Scope에서 관계가 명시적으로 제거된 경우 해당 관계를 제거할 수 있다. 객체 자체는 미발견 정책을 따른다.

수동 Export 또는 부분 API Scope에서는 파일이나 응답에 없는 관계를 제거하지 않는다.

### 14.4 NetBox 쓰기 순서

ResourceBatch는 의존성 순서에 따라 적용한다.

```text
1. CloudAccount, Region, Site
2. VRF, Prefix
3. VirtualMachine, CloudNetworkInterface, VMInterface
4. IPAddress
5. CloudLoadBalancer, ManagedDatabase, ObjectBucket
6. Domain, DNSZone, DNSRecord
7. BusinessService와 Owner 연결
8. 나머지 관계
9. last_seen_at과 sync_state
```

부모 객체 적용에 실패하면 해당 객체를 참조하는 자식은 적용하지 않고 `blocked_by_dependency`로 기록한다. 관계 적용 실패는 객체 생성을 되돌리지 않으며, 재실행 시 관계 단계부터 안전하게 복구한다.

### 14.5 원자성과 복구

NetBox REST API 전체를 하나의 Database Transaction으로 묶을 수 없으므로 Batch 전체 Rollback은 시도하지 않는다.

- 적용 전에 모든 Schema와 참조 가능성을 검증한다.
- 각 객체 Upsert는 멱등하게 수행한다.
- CollectionRun에 마지막 완료 단계를 Checkpoint로 저장한다.
- Worker가 중단되면 같은 Batch와 Checkpoint로 재개한다.
- 성공한 객체를 보상 삭제하지 않는다.
- 실패한 객체와 관계만 재시도한다.
- 적용 완료 후 NetBox를 다시 읽어 생성, 갱신, 관계 결과를 검증한다.

### 14.6 충돌 처리

사용자가 NetBox에서 클라우드 관리 필드를 직접 변경한 뒤 수집이 실행되면 클라우드 관측값으로 복원하고 `manual_drift_overwritten` 변경 사유를 기록한다.

사용자 관리 필드는 수집기가 변경하지 않는다. 어떤 필드가 어느 범주에 속하는지는 Schema Version에 고정하고 실행 중 동적으로 바꾸지 않는다.

## 15. 미발견과 삭제

미발견은 완전하게 성공한 API Scope에만 적용한다.

```text
첫 번째 미발견
-> sync_state = stale_candidate

연속 3회 또는 7일 이상 미발견
-> sync_state = inactive

inactive 이후
-> 자동 최종 삭제 없음
```

최종 삭제는 권한이 있는 사용자가 별도 정리 작업을 실행할 때만 수행한다.

계정이 비활성화되거나 자격증명이 만료된 경우 기존 리소스 상태를 변경하지 않고 계정 수집 상태만 `failed`로 표시한다.

## 16. 실행 상태와 오류 처리

### 16.1 CollectionRun 상태

```text
received
validating
normalizing
previewing
applying
success
partial
failed
```

### 16.2 오류 격리

- 특정 리전 실패 시 다른 리전 수집은 계속한다.
- 특정 서비스 실패 시 다른 서비스 수집은 계속한다.
- 성공한 Scope만 NetBox에 반영한다.
- 실패한 Scope의 기존 데이터는 변경하지 않는다.
- 하나 이상의 Scope가 실패하면 실행 상태를 `partial`로 기록한다.

### 16.3 재시도

- Rate Limit, 네트워크 오류, 5xx는 지수 백오프로 최대 3회 재시도한다.
- 인증 실패는 재시도하지 않고 즉시 실패 처리한다.
- 파싱 오류는 자동 재시도하지 않는다.
- 각 오류에는 run_id, account_id, scope, provider_request_id를 기록한다.
- 자격증명과 민감한 응답 값은 기록하지 않는다.

## 17. 동시성과 멱등성

- 계정 단위 적용 잠금을 사용한다.
- 같은 계정의 API 수집과 파일 반영을 동시에 실행하지 않는다.
- 같은 `cloud_uid`는 하나의 NetBox 객체에만 대응한다.
- 같은 ResourceBatch를 반복 적용해도 최종 상태가 달라지지 않는다.
- 동일 파일 해시는 한 번만 적용한다.
- Worker 중단 후 재실행해도 이미 반영된 객체를 중복 생성하지 않는다.

## 18. 보안

- 모든 클라우드 권한은 읽기 전용으로 제한한다.
- AWS는 장기 사용자 키보다 AssumeRole 임시 자격증명을 사용한다.
- NAVER Cloud Platform은 조회 전용 Sub Account 키를 사용한다.
- 자격증명 값은 Secret Manager에 보관한다.
- control-db에는 `credential_ref`만 저장한다.
- 로그와 오류 응답에서 Secret Key와 임시 토큰을 제거한다.
- 수동 업로드는 인증된 내부 운영자만 수행한다.
- 업로드 파일의 크기, 확장자, MIME 유형, 실제 파일 구조를 검사한다.
- 매크로 포함 XLSX는 거부한다.
- 원본 업로드 파일은 접근이 제한된 저장소에 30일간 보관한 후 삭제한다.

## 19. 운영과 관측

각 실행은 다음 지표를 제공한다.

- 실행 시간
- 공급자 API 호출 수
- Scope별 성공과 실패 수
- 생성, 갱신, 관계 변경, 미발견 수
- API 재시도 수
- 파싱 경고와 오류 수
- 마지막 성공 수집 시각

로그는 `run_id`, `account_id`, `scope`로 검색할 수 있어야 한다.

계정 화면에는 다음 상태를 표시한다.

- 마지막 성공 시각
- 다음 예정 실행 시각
- 최근 실행 결과
- 연속 실패 횟수
- 비활성 자격증명 여부
- 최근 업로드 파일

## 20. 제어 API와 권한

### 20.1 인증

사내 OIDC 또는 SSO를 Reverse Proxy에서 처리하고, 검증된 사용자와 Group 정보를 inventory-api에 전달한다. 개발 환경에서만 로컬 계정을 허용한다.

### 20.2 역할

| 역할 | 권한 |
|---|---|
| Viewer | 계정 상태, 실행 이력, 미리보기 조회 |
| Operator | Viewer 권한, 즉시 수집, 파일 업로드, 미리보기 적용 |
| Administrator | Operator 권한, 계정 설정, 스케줄, Credential Reference, Mapping 관리 |

최종 객체 삭제는 Administrator만 요청할 수 있으며, 삭제 대상 미리보기와 두 번째 확인을 요구한다.

### 20.3 내부 API

```text
GET    /accounts
POST   /accounts
PATCH  /accounts/{account_id}
POST   /accounts/{account_id}/collect
GET    /runs
GET    /runs/{run_id}
POST   /imports
GET    /imports/{import_id}/preview
POST   /imports/{import_id}/apply
GET    /mappings/tags
PUT    /mappings/tags/{mapping_id}
GET    /mappings/owners
PUT    /mappings/owners/{mapping_id}
```

쓰기 요청에는 사용자 ID, 요청 ID, 대상 계정, 결과를 Audit Log에 기록한다. 서버 렌더링 Form은 CSRF 보호를 적용한다.

## 21. 배포

초기 배포 단위는 다음과 같다.

```text
Docker Compose
- inventory-api
- inventory-worker
- PostgreSQL
- API와 Worker 전용 Artifact Volume
```

운영 환경의 외부 의존성은 NetBox, 사내 Secret Manager, S3 호환 Artifact Store다.

API와 Worker는 같은 애플리케이션 이미지를 사용하되 실행 명령을 분리한다. Worker가 PostgreSQL 작업 큐와 스케줄을 처리한다. 초기에는 하나의 Worker만 사용하며 계정 수와 실행 시간이 증가할 때 Worker 수를 늘린다.

NetBox는 Custom Objects 플러그인과 Redis를 포함한 별도 운영 단위다. 수집 서비스는 자체 Redis를 추가하지 않고 PostgreSQL 작업 큐를 사용한다.

### 21.1 네트워크 경계

- inventory-api는 내부 사용자 네트워크에서만 접근 가능하다.
- inventory-worker만 클라우드 API, NetBox, Secret Manager, Artifact Store에 접근한다.
- PostgreSQL은 API와 Worker에서만 접근한다.
- Artifact Store Bucket은 Public Access를 차단한다.
- 공공망에서 중앙 Worker의 직접 접근이 금지되면 해당 Realm 전용 Worker를 같은 코드로 별도 배치한다.

### 21.2 Database와 Artifact Backup

- control-db는 매일 Backup하고 30일 보관한다.
- NetBox Backup은 기존 NetBox 운영 정책을 따른다.
- Artifact Store 원본 파일은 복구 데이터가 아니라 감사와 재처리 보조 자료로 취급한다.
- 원본 파일이 만료되어도 이미 반영된 NetBox 자산은 영향을 받지 않는다.

## 22. 테스트 전략

### 22.1 단위 테스트

- AWS 응답 Fixture 정규화
- NAVER Cloud Platform 민간 응답 Fixture 정규화
- NAVER Cloud Platform 공공 응답 Fixture 정규화
- CSV, JSON, XLSX Parser
- TagMapping 우선순위
- cloud_uid 생성
- 상태 매핑
- 관계 생성

### 22.2 병합 테스트

- 동일 배치 반복 적용
- API와 Export 간 최신 관측값 선택
- 빈 값이 기존 값을 지우지 않는지 검증
- 사용자 관리 필드 보존
- 부분 Scope에서 관계와 객체 보존
- 완전한 Scope에서만 미발견 계산

### 22.3 통합 테스트

- 임시 NetBox 인스턴스에 객체 생성과 갱신
- VRF, Prefix, VMInterface, IP 관계 생성
- Custom Object 관계 생성
- Worker 중단 후 재실행
- 계정 단위 잠금

### 22.4 오류 테스트

- Rate Limit
- API Timeout
- 인증 실패
- 특정 Region 실패
- 특정 서비스 실패
- 손상된 파일
- 지원하지 않는 Export 버전
- 필수 열 누락
- 중복 파일

### 22.5 보안 테스트

- 허용되지 않은 API Host 차단
- Secret Log Redaction
- 권한 없는 수집 실행과 파일 적용 차단
- MIME 위장 파일 차단
- 매크로 포함 XLSX 차단
- CSV Formula Injection 문자열 무해화
- 만료된 미리보기 적용 차단
- 다른 계정의 Import ID 접근 차단

### 22.6 호환성 테스트

- NetBox 4.5.2 이상 4.5.x와 Custom Objects 0.6.x
- NetBox 4.6.x와 Custom Objects 0.6.x
- Portable Schema Preview와 Apply
- Custom Object Owner와 Core Object Owner
- Custom Objects REST API의 CRUD, Filter, 관계 조회
- 공급자 API Fixture의 이전 Schema와 현재 Schema
- Export Parser Profile의 Header 변경 감지

## 23. 비기능 기준

### 23.1 초기 규모 기준

- 최대 50개 클라우드 계정
- 최대 100,000개 정규화 리소스
- 계정당 최대 30개 Region
- 파일당 최대 100 MB
- 업로드 배치당 최대 20개 파일
- 계정별 수집 적용은 직렬화
- 서로 다른 계정은 Worker 수 범위에서 병렬 처리

초기 규모를 넘으면 Resource Type별 Worker 분리와 NetBox Bulk API 사용을 우선 검토한다.

### 23.2 응답과 처리

- 계정, 실행 이력, 오류 목록 화면은 Pagination을 사용한다.
- 수집과 Import 적용은 비동기 Job으로 실행한다.
- 쓰기 요청은 Job ID를 즉시 반환한다.
- UI는 실행 상태를 10초 이내 주기로 갱신한다.
- 동일 Fingerprint 객체는 NetBox 쓰기를 생략한다.

### 23.3 보존 기간

| 데이터 | 보존 기간 |
|---|---|
| 업로드 원본 | 30일 |
| 선택적 API 진단 응답 | 7일 |
| CollectionRun과 Scope 결과 | 180일 |
| ChangeSummary와 Audit Log | 1년 |
| NetBox 변경 이력 | NetBox 운영 정책 |

### 23.4 가용성

수집 시스템 장애가 클라우드 인프라 운영에 영향을 주지 않아야 한다. Collector는 읽기 전용이며, 수집 서비스가 중단되어도 NetBox 기존 데이터와 클라우드 리소스는 유지된다.

### 23.5 PoC와 사내 도입 경계

PoC는 합성 Fixture와 별도 테스트 계정으로 실행한다. 실제 운영 계정과 내부 데이터를 연결하려면 다음 운영 준비 항목을 별도 승인해야 한다.

- 사내 SSO와 Group 기반 권한 연결
- Secret Manager와 조회 전용 Credential 발급
- NetBox, control-db, Artifact Store Backup과 복구 훈련
- 공공과 민간 Realm의 네트워크 접근 검토
- 운영 책임자, 장애 대응 절차, 보존 기간 확정
- 실제 Export 파일과 내부 데이터의 저장 위치 및 접근 통제

## 24. 완료 기준

1차 구현은 다음 조건을 모두 만족할 때 완료로 판단한다.

1. AWS, NAVER Cloud Platform 민간, NAVER Cloud Platform 공공 계정을 등록할 수 있다.
2. 계정별 자동 수집을 즉시 실행하거나 스케줄링할 수 있다.
3. VPC, Subnet, VM, NIC, IP, Load Balancer, DNS, Database, Bucket을 NetBox에서 조회할 수 있다.
4. 공급자 Export 파일을 업로드하고 반영 전 변경 내용을 확인할 수 있다.
5. 동일 API 결과나 파일을 반복 처리해도 중복 객체가 생기지 않는다.
6. 특정 Region 또는 서비스 수집 실패가 기존 정상 데이터를 비활성화하지 않는다.
7. IP에서 계정까지 네트워크 관계를 추적할 수 있다.
8. Domain에서 BusinessService와 Owner까지 서비스 관계를 추적할 수 있다.
9. Kubernetes와 Amazon ECS Collector를 후속으로 추가할 수 있는 인터페이스가 정의되어 있다.
10. 모든 필수 단위 테스트와 통합 테스트가 통과한다.
11. Viewer, Operator, Administrator 권한이 분리되어 있다.
12. 100,000개 Fixture 리소스를 중복 없이 처리한다.
13. Worker 중단 후 같은 Checkpoint에서 재개할 수 있다.
14. Secret과 Credential이 Log, Database, Preview에 노출되지 않는다.
15. Version Control의 Portable Schema를 새 NetBox 환경에 Preview하고 비파괴적으로 적용할 수 있다.

## 25. 주요 위험과 대응

| 위험 | 대응 |
|---|---|
| 공급자 API 또는 Export Header 변경 | Schema Version과 Parser Profile, Golden Fixture로 조기 감지 |
| 불완전한 수집 결과로 대량 비활성화 | 완전한 API Scope에서만 미발견 계산 |
| API와 수동 파일의 충돌 | 관측 시각과 신뢰도에 따른 필드 병합 |
| NetBox 쓰기 도중 Worker 중단 | Checkpoint와 멱등 Upsert로 재개 |
| 태그 표기 차이로 서비스와 Owner 중복 | 승인된 Mapping만 연결하고 Hint는 별도 보존 |
| NetBox Upgrade로 API 또는 Custom Object 호환성 변경 | 지원 버전 Matrix와 Upgrade 통합 테스트 |
| Custom Object 스키마 변경으로 Column 손실 | Portable Schema Preview와 파괴적 변경 승인 절차 |
| NCP 공공망 접근 제한 | Realm 전용 Worker 또는 수동 Export 경로 사용 |
| 업로드 파일을 통한 공격 | 형식 검증, 매크로 차단, 크기 제한, Formula 무해화 |
| Secret 노출 | Secret Manager, 최소 권한, Log Redaction |
| NetBox를 범용 CMDB로 과도하게 확장 | 1차 Scope와 비목표를 유지하고 신규 리소스는 별도 설계 검토 |

## 26. 결정 사항 요약

- 독립 수집 서비스를 사용하고 NetBox 플러그인에 수집 로직을 넣지 않는다.
- Python 3.12, FastAPI, PostgreSQL을 초기 기술 기준으로 사용한다.
- NetBox를 유일한 정규화 자산 저장소로 사용한다.
- 수집 서비스는 PostgreSQL 작업 큐를 사용하고 별도 Redis를 도입하지 않는다.
- NetBox는 Custom Objects 플러그인의 운영 요구사항에 따라 자체 Redis를 사용한다.
- Custom Object Type은 Portable Schema JSON으로 버전 관리한다.
- 업로드 원본은 S3 호환 Artifact Store에 기간 제한으로 보관한다.
- API 자동 수집과 수동 Export 업로드를 모두 제공한다.
- 공통 ResourceBatch 이후 처리 코드를 공유한다.
- 클라우드 관측 필드와 사용자 관리 필드의 소유권을 분리한다.
- Owner와 BusinessService는 승인된 Mapping으로만 자동 연결한다.
- 수동 파일 누락으로는 객체를 비활성화하지 않는다.
- 완전한 API Scope에서만 미발견을 계산한다.
- inactive 객체를 자동으로 최종 삭제하지 않는다.
- Kubernetes와 Amazon ECS는 후속 범위로 분리한다.

## 27. 오픈소스 라이선스와 공개 저장소

이 프로젝트는 Apache License 2.0으로 공개한다. 저장소 루트의 `LICENSE`를 적용한다.

- 상업적 사용, 수정, 배포, 사적 사용을 허용한다.
- 재배포 시 License와 저작권 고지를 유지하고 변경 사항을 표시한다.
- Contributor의 특허 허여와 License의 특허 종료 조건을 따른다.
- Third-party Dependency는 각각의 License를 별도로 준수한다.
- 실제 Export 원본, 계정 ID, 내부 Domain, IP, 자격증명, NetBox Data를 Public Repository에 포함하지 않는다.
- 문서 예제, Parser Sample, 테스트 Fixture는 합성하거나 비식별화한 데이터만 사용한다.

## 28. 수동 Import PoC 구현 상태

이 설계의 1차 구현은 공급자 API 없이 파일을 수집하고 NetBox에서 조회하는 경로를 우선 완성했다.

구현된 구성은 다음과 같다.

- FastAPI 기반 Upload, Preview, Apply, Run 조회 API
- CSRF 보호가 적용된 최소 서버 렌더링 UI
- PostgreSQL 기반 Import 요청, immutable Preview, 작업 큐, 실행 이력
- 로컬 Docker Volume 기반 30일 Artifact 보관
- AWS Resource Explorer CSV Parser
- NCP Server, Public IP, Load Balancer, Object Storage XLSX Parser
- 전체 1차 리소스와 관계를 표현하는 표준 JSON Import Bundle
- NetBox 4.6 Core 객체와 Custom Objects 0.6 Portable Schema
- 의존 순서별 NetBox Upsert, ETag 충돌 재검증, Owner와 BusinessService 보존
- 중복 파일, 중복 Apply, 부분 Snapshot 누락 안전성
- Docker Compose 전체 흐름을 검증하는 재현 가능한 통합 테스트

수동 파일은 항상 부분 Snapshot으로 처리한다. 파일에 없는 리소스는 삭제하거나 비활성화하지 않는다. 필수 관계가 없는 요약 리소스는 잘못된 NetBox 객체를 만들지 않고 경고로 보존한다. 동일 Provider, Realm, Account ID와 동일 파일 내용은 기존 Import를 반환하며 동일 Batch hash의 Apply는 기존 Run을 반환한다.

`scripts/poc_import.sh`는 로컬 비밀 값 생성, Compose 기동, NetBox 스키마 적용, AWS와 NCP 및 표준 Bundle 통합 검증을 한 번에 수행한다. GitHub Actions의 필수 검사는 Lint, Type Check, 단위 및 API 테스트, 생성 Schema 일치 여부를 검사한다. 전체 Docker 통합 검사는 수동 Workflow로 분리한다.

아직 구현하지 않은 항목은 다음과 같다.

- AWS와 NCP API Collector 및 Scheduler
- SSO, Secret Manager, 외부 S3 호환 Artifact Store
- 운영 Backup, 고가용성, 감사 정책
- Kubernetes와 Amazon ECS 수집

실제 계정과 사내 데이터를 연결하기 전에는 23.5절의 운영 준비 항목을 별도로 승인해야 한다.

## 29. 참고 자료

- [NetBox Virtualization](https://netboxlabs.com/docs/netbox/features/virtualization/)
- [NetBox Resource Ownership](https://netboxlabs.com/docs/netbox/features/resource-ownership/)
- [NetBox Custom Objects](https://netboxlabs.com/docs/custom-objects/)
- [NetBox Custom Objects Installation](https://netboxlabs.com/docs/custom-objects/installation/)
- [NetBox Custom Objects Compatibility](https://github.com/netboxlabs/netbox-custom-objects/blob/main/COMPATIBILITY.md)
- [NetBox Custom Objects Portable Schema](https://netboxlabs.com/docs/custom-objects/portable-schema/)
- [NetBox Custom Objects REST API](https://netboxlabs.com/docs/custom-objects/rest-api/)
- [NetBox REST API](https://netboxlabs.com/docs/netbox/integrations/rest-api/)
- [AWS Cross Account Roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/tutorial_cross-account-with-roles.html)
- [AWS Resource Explorer CSV Export](https://docs.aws.amazon.com/resource-explorer/latest/userguide/managing-resources.html)
- [NAVER Cloud Platform Resource Manager Resource](https://guide.ncloud-docs.com/docs/en/resourcemanager-use-resource)
- [NAVER Cloud Platform Server List Download](https://guide.ncloud-docs.com/docs/en/server-screen-vpc)
- [NAVER Cloud Platform Public IP List Download](https://guide.ncloud-docs.com/docs/en/server-publicip-vpc)
- [NAVER Cloud Platform Load Balancer List Download](https://guide.ncloud-docs.com/docs/en/loadbalancer-screen-vpc)
- [NAVER Cloud Platform Object Storage Bucket List Download](https://guide.ncloud-docs.com/docs/en/objectstorage-use-screen)
- [NAVER Cloud Platform VPC API](https://api.ncloud-docs.com/docs/networking-vpc)
- [NAVER Cloud Platform Public VPC API](https://api-gov.ncloud-docs.com/docs/networking-vpc)
- [NAVER Cloud Platform Load Balancer API](https://api.ncloud-docs.com/docs/en/networking-vloadbalancer)
- [NAVER Cloud Platform Global DNS Record API](https://api.ncloud-docs.com/docs/en/networking-globaldns-record-getrecordlist)
- [NAVER Cloud Platform Public Global DNS Record API](https://api-gov.ncloud-docs.com/docs/networking-globaldns-record-getrecordlist)
- [NAVER Cloud Platform Object Storage API](https://api-gov.ncloud-docs.com/docs/storage-objectstorage)
- [NAVER Cloud Platform Cloud DB for MySQL API](https://api.ncloud-docs.com/docs/en/database-vmysql)
- [NAVER Cloud Platform Cloud DB for PostgreSQL API](https://api.ncloud-docs.com/docs/en/database-vpostgresql)
- [GitHub Repository Licensing](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)
