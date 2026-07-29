# NetBox Cloud Inventory

[한국어](README.ko.md) | English

Documentation maintenance: workflow and feature changes must update both `README.md` and `README.ko.md` in the same commit.

This open-source side project evaluates whether NetBox can provide a central place to organize and query infrastructure fragmented across AWS and NAVER Cloud Platform commercial and government environments. The project starts with synthetic data and test accounts as a proof of concept. Internal adoption will be considered only if the project demonstrates clear operational and discovery benefits.

Cloud API collection is the intended default path in the future. The project also supports manual uploads of CSV and XLSX files exported from provider consoles and a canonical JSON Import Bundle. Both paths use the same normalization and validation pipeline before data is applied to NetBox.

The current implementation is a PoC focused first on manual file collection and lookup. AWS and NCP API collectors have not been implemented yet.

## Quick Start

The following tools are required.

- Docker Engine or Docker Desktop with Docker Compose v2
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- OpenSSL
- curl

Install development dependencies exactly as pinned in the lock file.

```bash
uv sync --locked --all-groups
```

### Start an Empty Environment and Upload Real Exports

```bash
./scripts/start_local.sh
```

`start_local.sh` performs only the following actions.

- Generates local secrets if `.env` does not exist
- Sets the generated `.env` permissions to `0600`
- Builds Docker Compose images and starts the services
- Checks NetBox and Inventory API health
- Applies the NetBox Cloud Inventory schema

It does not overwrite or reset an existing `.env` or Docker Volume. It also does not load demo or test data, so this is the default command when you want to inspect files exported from AWS or NCP.

The services are available at the following addresses.

- Manual Import UI: `http://127.0.0.1:8080/ui/imports`
- Inventory API health: `http://127.0.0.1:8080/healthz`
- NetBox: `http://127.0.0.1:8000`

Apply real export files in this order.

1. Open the Manual Import UI.
2. Enter the Provider, Realm, Account ID, Export type, Exported at, and optional Region.
3. Upload an AWS CSV, NCP XLSX, or canonical JSON Import Bundle.
4. Review the expected creates, updates, warnings, and errors in Preview.
5. Run Apply while preserving the approved Batch hash.
6. Query the created or updated objects and relationships in NetBox.

See the [Manual import operations guide (Korean)](docs/manual-import.md) for file-specific input rules and missing-resource safety behavior.

### Load Synthetic Demo Data

Start the environment first, and then explicitly load the demo data.

```bash
./scripts/start_local.sh
./scripts/load_demo.sh
```

`load_demo.sh` does not start Docker Compose. It checks the running Inventory API and NetBox, uploads only the fixed synthetic data under `examples/demo/`, and runs Preview and Apply.

- AWS Resource Explorer CSV
- NCP government Server list XLSX
- Canonical JSON Import Bundle containing VPC, Subnet, VM, NIC, IP, Load Balancer, DNS, Database, and Object Storage relationships

Running the same demo files repeatedly reuses the existing Import and Run records instead of creating duplicate objects. The output shows each Import ID, Run ID, and apply summary.

### Run the Full Integration Verification

Use the following command to verify the complete Compose integration flow in development and CI.

```bash
./scripts/test_integration.sh
```

This command runs `start_local.sh`, loads synthetic AWS, NCP, and canonical Bundle data with randomized identifiers, and verifies idempotency, preservation of manually assigned Owners, and missing-resource safety. You do not need to run it when you only want to inspect your own files.

The existing `scripts/poc_import.sh` remains as a compatibility wrapper and delegates to `test_integration.sh`. New workflows should use the three purpose-specific scripts.

### Stop or Reset the Environment

The following command stops the containers while preserving named Docker Volumes and stored data.

```bash
docker compose down
```

Warning: the following command deletes Docker Volumes, including NetBox data, Import history, and uploaded Artifacts. Use it only when a complete reset of the local PoC environment is intended.

```bash
docker compose down -v
```

The following Parser profiles are supported.

- `aws.resource_explorer.csv.v1`
- `ncp.server_list.xlsx.v1`
- `ncp.public_ip_list.xlsx.v1`
- `ncp.load_balancer_list.xlsx.v1`
- `ncp.object_storage_bucket_list.xlsx.v1`
- `canonical.import_bundle.v1`

Before using real exports, review the operations guide for file-specific limitations, Preview approval, missing-resource safety, and security precautions.

## Goals

- Query infrastructure from multiple cloud accounts in one NetBox instance
- Normalize provider-specific resource structures and field names into a common model
- Track relationships between accounts, VPCs, Subnets, VMs, NICs, IPs, Load Balancers, DNS, Databases, and Object Storage
- Connect business services and owning teams by using cloud tags
- Prevent data loss caused by cloud API failures or incomplete export files
- Guarantee idempotency for repeated collection and repeated uploads of the same data

## Initial Scope

### Supported Providers and Environments

- AWS
- NAVER Cloud Platform commercial
- NAVER Cloud Platform government

### Collected Resources

- Cloud accounts
- Regions and Zones
- VPCs and Subnets
- VMs and Network Interfaces
- Private and public IP addresses
- Load Balancers
- Domains, DNS Zones, and DNS Records
- AWS RDS and Aurora
- NAVER Cloud Platform Cloud DB for MySQL and PostgreSQL
- AWS S3 and NAVER Cloud Platform Object Storage
- Cloud tags
- Business services and owning teams that can be identified from tags

### Future Scope

- Kubernetes
- Amazon ECS
- Kubernetes Namespaces, Services, Ingresses, and Workloads
- Additional provider-specific managed services

## Collection Methods

### Future Automated Collection

A scheduler will iterate through registered accounts and Regions and call provider APIs with read-only permissions.

- AWS will use a per-account read-only role with STS AssumeRole
- NAVER Cloud Platform will use read-only Sub Account credentials for commercial and government environments
- The default collection interval will be six hours per account
- On-demand collection will also be supported

### Manual Upload

Upload files exported from the AWS or NAVER Cloud Platform console, or upload this project's canonical Import Bundle.

- Supports AWS Resource Explorer CSV
- Supports NAVER Cloud Platform service console XLSX
- Supports the canonical JSON Import Bundle for complete resources and relationships
- Shows expected creates, updates, and errors before Apply
- Prevents duplicate application of the same file
- Does not delete fields or resources absent from a file
- Supports initial registration, network-separated environments, and API outage scenarios

## Processing Flow

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

## Resource Identity

The same resource is identified by a `cloud_uid` composed of the following values.

```text
provider
+ realm
+ account_id
+ region
+ resource_type
+ external_id
```

Examples follow.

```text
aws:commercial:123456789012:ap-northeast-2:virtual_machine:i-012345
ncp:government:account-01:KR:virtual_machine:12345678
```

## NetBox Mapping

| Cloud resource | NetBox target |
|---|---|
| Cloud account | CloudAccount Custom Object |
| Region | Region |
| Availability Zone | Site |
| VPC | VRF |
| Subnet | Prefix |
| VM | VirtualMachine |
| Every NIC | CloudNetworkInterface Custom Object |
| NIC attached to a VM | CloudNetworkInterface and its linked VMInterface |
| Private and public IP address | IPAddress |
| Load Balancer | CloudLoadBalancer Custom Object |
| Managed Database | ManagedDatabase Custom Object |
| Object Storage Bucket | ObjectBucket Custom Object |
| Domain and DNS | Domain, DNSZone, DNSRecord Custom Object |
| Business service | BusinessService Custom Object |
| Owning team | NetBox Owner |

## Safe Synchronization Principles

- Preserve existing NetBox data when an API call fails
- Determine successful scope at the service or Region level
- Calculate missing resources only within a completely successful API collection scope
- Do not calculate missing resources from omissions in manually uploaded files
- Mark the first missing observation as `stale_candidate`
- Mark a resource as `inactive` after three consecutive misses or seven days
- Never let a collector permanently delete a NetBox object automatically
- Preserve user-entered values such as Owner, BusinessService, descriptions, and operational notes

## Components

- `inventory-api`: account registration, manual upload, change preview, and execution history
- `inventory-worker`: API collection, file parsing, normalization, comparison, and NetBox updates
- `control-db`: account configuration, job queue, execution status, file metadata, and change summaries
- `artifact-store`: storage interface for original uploads. The PoC uses a dedicated local volume, while production can use an S3-compatible store
- `netbox`: storage and discovery for normalized infrastructure assets and relationships

The initial implementation uses Python 3.12, FastAPI, and PostgreSQL. It does not duplicate the entire asset inventory in a separate database. PostgreSQL stores only control-plane information.

NetBox 4.5 or later is required for Owner support, and the initial verification target is version 4.6. Cloud-specific resources are represented as Custom Object Types from the `netboxlabs-netbox-custom-objects` plugin instead of NetBox Core features. The initial compatibility combination is NetBox 4.6.x with Custom Objects 0.6.x.

The collection service is not implemented as a NetBox plugin. Only the Custom Object Type schema is versioned as Portable Schema JSON, and the collection service uses the NetBox and Custom Objects REST APIs.

## Security Principles

- Restrict cloud collection permissions to read-only access
- Do not store credential values in the application database
- Store only a Secret Manager `credential_ref` in account configuration
- Do not write Access Keys, Secret Keys, or temporary tokens to logs
- Allow manual uploads only for authorized internal users
- Validate upload size, filename extension, and actual content format together
- Limit each file to 100 MB and each batch to 20 files
- Retain original upload files for 30 days by default

## Project Status

The current stage is a manual Import PoC and is not intended for production use. It implements a FastAPI UI and API, PostgreSQL job queue, Worker, NetBox 4.6, Custom Objects Portable Schema, AWS CSV Parser, NCP XLSX Parser, canonical JSON Parser, and idempotent Upsert.

Automated tests cover Preview Batch hash approval, duplicate uploads and duplicate Apply operations, preservation of manually assigned Owners, and the rule that omissions in manual files must not deactivate resources. The default GitHub Actions quality checks exclude Docker integration tests, while a manually triggered workflow provides complete Compose verification.

The PoC uses synthetic Fixtures and separate test accounts. Before internal adoption, real account integration, SSO, Secret Manager, Backup, access control, and operational ownership must be validated in a separate production-readiness phase.

## Design Documents

- [NetBox Cloud Inventory design (Korean)](docs/superpowers/specs/2026-07-28-netbox-cloud-inventory-design.md)
- [Manual import operations guide (Korean)](docs/manual-import.md)
- [Bilingual README operations design (Korean)](docs/superpowers/specs/2026-07-30-bilingual-readme-design.md)
- [Import Bundle JSON Schema](schemas/import-bundle-v1.schema.json)

## License

This project is released under the [Apache License 2.0](LICENSE). Commercial use, modification, distribution, and private use are permitted under the license terms.

Do not commit real cloud export files, account IDs, internal Domains, IP addresses, credentials, or NetBox Data to the public repository. Examples and test Fixtures use synthetic data only.
