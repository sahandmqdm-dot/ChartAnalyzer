# Cloud-Ready Binance Data Architecture

## Deployment model

The collector is portable, not local-only. It can run on a Windows workstation today and later run unchanged on a Linux VM, container, cloud VM, or managed compute service. Its filesystem data root is configurable through CHARTANALYZER_DATA_DIR; it has no fixed machine path.

The supported first cloud pattern is a durable local spool plus asynchronous object-storage archival:

1. WebSocket events are appended to the local raw spool first.
2. Spot and USD-M remain separate namespaces and instruments.
3. At UTC day rollover, the previous day's JSONL partitions become eligible for archival.
4. binance_archive_sync verifies the complete SHA-256 event chain before upload.
5. Verified closed partitions are copied to S3-compatible object storage using content-addressed keys. Retries are deterministic; changed bytes create a different key.
6. The local copy is retained. Cloud upload failure never blocks or changes live capture.

This keeps cloud/network latency out of the WebSocket receive path while enabling cloud-hosted capture or cloud archival.

## Object storage support

Install the optional client:

    python -m pip install boto3

Configure credentials through the standard AWS credential chain (environment, profile/SSO, or cloud instance/workload role). Never store access keys in the repository or config.json.

Example on Windows PowerShell:

    $env:CHARTANALYZER_DATA_DIR = "F:\ChartAnalyzerData\binance_raw"
    $env:CHARTANALYZER_ARCHIVE_BUCKET = "your-private-bucket"
    $env:CHARTANALYZER_ARCHIVE_PREFIX = "chartanalyzer/raw"
    # Optional for S3-compatible providers:
    $env:CHARTANALYZER_S3_ENDPOINT_URL = "https://your-object-storage-endpoint"
    python -m binance_archive_sync --dry-run
    python -m binance_archive_sync

For AWS, omit the endpoint URL and use a region/profile or instance role. For a compatible provider, supply its endpoint and supported credentials. The SDK transfer manager can use multipart upload for large partitions.

## Object layout and integrity

Objects are partitioned by market/stream/date and include the full file SHA-256 in the key. Metadata includes schema version, event count, market, stream, partition date, file digest and final event hash-chain head.

Before upload, the complete JSONL file must pass HashChainJSONLStore.verify_file. Invalid, empty, malformed or tampered partitions are refused. The active UTC-day partition is never discovered for upload. The uploader never deletes local files.

Content-addressed keys make retries safe, but the object store must still be configured with private access, encryption, lifecycle/retention policy, versioning or object-lock as required. Hash chains provide tamper evidence, not administrator-proof immutability or proof of provider retention.

## Future cloud-native deployment

The same collector can run on cloud compute by setting CHARTANALYZER_DATA_DIR to a persistent attached volume. The archive sync process can run as a separate scheduled job/container. This supports workstation capture with cloud backup, cloud VM/container capture, multi-provider replication, and later analytical ingestion without changing raw event capture.

Do not mount one writable data directory concurrently into multiple collector instances. Each instance needs an explicit ownership/lease strategy before horizontal scaling. Partition names alone are not distributed coordination.

## Current boundaries

This is cloud-ready archival and deployment portability, not yet a fully managed distributed data platform. Still required before production cloud operations:
- tested provider credentials/permissions and multipart behavior;
- retry scheduling, upload audit ledger and remote checksum/read-back verification;
- retention, versioning/object-lock, restore drills and cost controls;
- a persistent shared queue/lease before multiple workers;
- replay/Parquet pipeline and monitoring/alerting;
- Windows and cloud VM fault-injection tests.

Cloud archival does not start or alter the 42-day prospective holdout. Do not start that clock until collector recovery, order-book snapshot orchestration, replay, tests, frozen configuration and backup/restore validation are complete.
