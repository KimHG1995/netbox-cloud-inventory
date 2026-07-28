# NetBox Cloud Inventory 설계

작성일: 2026-07-28

## 1. 배경

사내 인프라는 AWS와 NAVER Cloud Platform의 여러 계정 및 공공과 민간 환경에 분산되어 있다. 각 클라우드 콘솔은 해당 공급자와 계정 안에서는 상세 정보를 제공하지만, 다음과 같은 질문에 한 곳에서 답하기 어렵다.

- 특정 IP가 어느 계정, VPC, Subnet, VM에 연결되어 있는가
- 특정 Domain이 어느 Load Balancer, VM, Database, 업무 서비스로 이어지는가
- 특정 업무 서비스가 사용하는 인프라와 담당 팀은 무엇인가
- 서로 다른 계정에서 동일하거나 겹치는 CIDR을 사용하는가
- 수집이 실패했거나 오래된 데이터는 무엇인가

이 프로젝트는 NetBox를 중앙 인프라 장부로 사용하여 파편화된 정보를 정규화하고 조회할 수 있게 한다. 클라우드가 실행 상태의 원본이며 NetBox는 읽기 중심의 통합 System of Record로 동작한다.

## 2. 목표

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
- NAVER Cloud Platform Cloud DB
- AWS S3 Bucket
- NAVER Cloud Platform Object Storage Bucket
- 클라우드 태그
- 태그에서 식별되는 BusinessService와 Owner

### 5.3 후속 확장

- Kubernetes Cluster와 하위 리소스
- Amazon ECS Cluster와 하위 리소스
- 공급자별 추가 관리형 서비스

후속 리소스는 `Collector`, `FileParser`, `Normalizer` 인터페이스를 구현하는 방식으로 추가한다.

## 6. 전체 아키텍처

NetBox는 4.5 이상을 요구하며 초기 검증 기준 버전은 4.6이다. Owner 기능은 NetBox 코어를 사용하고, 클라우드 고유 리소스는 NetBox Custom Objects로 정의한다.

```text
자동 입력
Scheduler
  -> Account Registry
  -> AWS Collector 또는 NCP Collector
  -> ResourceBatch

수동 입력
Upload API
  -> File Validator
  -> AWS 또는 NCP File Parser
  -> ResourceBatch

공통 처리
ResourceBatch
  -> Schema Validator
  -> Resource Normalizer
  -> Reconciler
  -> NetBox Writer
  -> CollectionRun Result
```

### 6.1 inventory-api

내부 운영자를 위한 제어 API와 최소 관리 화면을 제공한다.

- 계정 등록과 상태 조회
- 자동 수집 즉시 실행
- 계정별 스케줄 설정
- Export 파일 업로드
- 반영 전 변경 미리보기
- 실행 이력과 오류 조회

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

### 6.3 control-db

PostgreSQL을 사용하며 인프라 자산 전체를 저장하지 않는다.

- AccountConfig
- CollectionSchedule
- CollectionRun
- CollectionScopeResult
- SourceFile
- ChangeSummary
- CredentialReference

실제 정규화된 자산과 관계는 NetBox에 저장한다.

### 6.4 NetBox

다음 역할을 담당한다.

- 정규화된 리소스 저장
- 리소스 관계 탐색
- 검색과 필터
- Owner와 BusinessService 보강
- 변경 이력
- 사용자 권한

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

## 9. 수동 Export 파일 업로드

### 9.1 지원 형식

- CSV
- JSON
- 매크로가 없는 XLSX

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

## 10. 공통 ResourceBatch

```text
ResourceBatch
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
- raw_reference
```

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

## 12. NetBox 매핑

| 공통 리소스 | NetBox 모델 |
|---|---|
| CloudAccount | CloudAccount Custom Object |
| Region | Region |
| Zone | Site |
| VPC | VRF |
| Subnet | Prefix |
| VirtualMachine | VirtualMachine |
| 연결된 NetworkInterface | VMInterface |
| 분리된 NetworkInterface | CloudNetworkInterface Custom Object |
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
last_seen_at
sync_state
```

`cloud_uid`는 유일 식별자로 사용한다.

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

키 비교는 대소문자를 구분하지 않지만 원본 키와 값은 변경하지 않는다. 둘 이상의 키가 충돌하면 계정별 TagMapping 설정의 우선순위를 사용하고 경고를 기록한다.

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

## 20. 배포

초기 배포 단위는 다음과 같다.

```text
Docker Compose
- inventory-api
- inventory-worker
- PostgreSQL
```

외부 의존성은 NetBox와 사내 Secret Manager다.

API와 Worker는 같은 애플리케이션 이미지를 사용하되 실행 명령을 분리한다. 초기에는 하나의 Worker만 사용하며 계정 수와 실행 시간이 증가할 때 Worker 수를 늘린다.

## 21. 테스트 전략

### 21.1 단위 테스트

- AWS 응답 Fixture 정규화
- NAVER Cloud Platform 민간 응답 Fixture 정규화
- NAVER Cloud Platform 공공 응답 Fixture 정규화
- CSV, JSON, XLSX Parser
- TagMapping 우선순위
- cloud_uid 생성
- 상태 매핑
- 관계 생성

### 21.2 병합 테스트

- 동일 배치 반복 적용
- API와 Export 간 최신 관측값 선택
- 빈 값이 기존 값을 지우지 않는지 검증
- 사용자 관리 필드 보존
- 부분 Scope에서 관계와 객체 보존
- 완전한 Scope에서만 미발견 계산

### 21.3 통합 테스트

- 임시 NetBox 인스턴스에 객체 생성과 갱신
- VRF, Prefix, VMInterface, IP 관계 생성
- Custom Object 관계 생성
- Worker 중단 후 재실행
- 계정 단위 잠금

### 21.4 오류 테스트

- Rate Limit
- API Timeout
- 인증 실패
- 특정 Region 실패
- 특정 서비스 실패
- 손상된 파일
- 지원하지 않는 Export 버전
- 필수 열 누락
- 중복 파일

## 22. 완료 기준

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

## 23. 결정 사항 요약

- 독립 수집 서비스를 사용하고 NetBox 플러그인에 수집 로직을 넣지 않는다.
- Python 3.12, FastAPI, PostgreSQL을 초기 기술 기준으로 사용한다.
- NetBox를 유일한 정규화 자산 저장소로 사용한다.
- API 자동 수집과 수동 Export 업로드를 모두 제공한다.
- 공통 ResourceBatch 이후 처리 코드를 공유한다.
- 클라우드 관측 필드와 사용자 관리 필드의 소유권을 분리한다.
- 수동 파일 누락으로는 객체를 비활성화하지 않는다.
- 완전한 API Scope에서만 미발견을 계산한다.
- inactive 객체를 자동으로 최종 삭제하지 않는다.
- Kubernetes와 Amazon ECS는 후속 범위로 분리한다.

## 24. 참고 자료

- [NetBox Virtualization](https://netboxlabs.com/docs/netbox/features/virtualization/)
- [NetBox Resource Ownership](https://netboxlabs.com/docs/netbox/features/resource-ownership/)
- [NetBox Custom Objects](https://netboxlabs.com/docs/custom-objects/)
- [NetBox REST API](https://netboxlabs.com/docs/netbox/integrations/rest-api/)
- [AWS Cross Account Roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/tutorial_cross-account-with-roles.html)
- [AWS Resource Explorer CSV Export](https://docs.aws.amazon.com/resource-explorer/latest/userguide/managing-resources.html)
- [NAVER Cloud Platform VPC API](https://api.ncloud-docs.com/docs/networking-vpc)
- [NAVER Cloud Platform Public VPC API](https://api-gov.ncloud-docs.com/docs/networking-vpc)
- [NAVER Cloud Platform Object Storage API](https://api-gov.ncloud-docs.com/docs/storage-objectstorage)
- [NAVER Cloud Platform Cloud DB for MySQL API](https://api.ncloud-docs.com/docs/en/database-vmysql)
- [NAVER Cloud Platform Cloud DB for PostgreSQL API](https://api.ncloud-docs.com/docs/en/database-vpostgresql)
