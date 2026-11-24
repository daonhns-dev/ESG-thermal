# Shared original ATT/ATG direct reads

> SUPERSEDED INITIAL DRAFT: the URL-to-path mapping described below was removed.
> Current code only reads explicit att.path/atg.path directly; URL/object_key
> requests keep their original download/fetch behavior. Use
> [Q15 v1.1 plan-A draft](Q15_SHARED_PATH_V1_1_DRAFT.md) for the current contract.
> No version of this change has been deployed to the server.

Status: local implementation; not enabled on the test server. VTI's portal storage
implementation is not in this repository. The current MinIO object directories
are not supported as plain original files.

Validation (2026-11-18): Linux storage/worker suite: 14 passed, including no-fetch
success and original preservation after success, failure and cancellation. Other
regression run: 43 passed, one static OpenAPI equality failure; the observed diff
was only HTTP 422 descriptions (Unprocessable Content vs Unprocessable Entity).
The golden algorithm comparison could not collect because the temporary Linux
environment lacks PyTorch. Compose overlay merge validation passed. Real portal
publication and end-to-end direct-read rollout remain unverified.

## Prerequisite

VTI must publish complete plain ATT/ATG files in a shared filesystem directory
before submitting INT-003. Uploading only to the existing MinIO backing store does
not meet this requirement. Published files must remain immutable and available
through queueing, analysis and retries. VTI must confirm its upload, retrieval,
retention and deletion behavior before switching the portal storage implementation.
Publishing a second copy alongside MinIO would preserve a duplicate and would not
fulfil the objective of storing the original only once.

The current request JSON stays unchanged. For URL inputs, the URL path (decoded,
including the bucket component, excluding the signature query) maps beneath the
configured plain-file root. For example:

```text
http://portal-minio:9000/heatpipe-portal/raw/2026/11/id.att?...
 -> /shared-input/heatpipe-portal/raw/2026/11/id.att
```

Object-key requests map the relative object key beneath the same root. Configure
the publication layout to match whichever request type the portal sends. The
worker enforces the existing URL host allowlist and optional size/SHA-256 checks.
An enabled direct-read mode fails on a missing file, object directory, invalid
path or integrity mismatch; it never falls back to a download.

## Behavior

The worker links each verified original into its existing per-job input directory
under the monitoring data ID. No original bytes are copied or downloaded. This
preserves existing batch filename requirements and result metadata. The original
mount is read-only. Existing temporary job cleanup removes links and temporary
outputs, never the shared originals. Results and SQLite remain in the analysis
data volume; do not replace that volume with the MinIO backing directory.

INT-003/004/001 request/response structures, job IDs, state values and progress
sequence stay unchanged. DOWNLOADING is retained for compatibility, with a
"Preparing shared ATT/ATG files" status message. Existing retry and cancellation
flow stays in effect. URL expiry is not checked by an HTTP GET in this mode: the
trusted shared publication and backend authentication become the access boundary.

## Deployment gate

Only after the publication prerequisite has been verified, set
`HEATPIPE_SHARED_INPUT_HOST_ROOT` to the host's plain-file publication directory
and add `-f compose.shared-input.yaml` after the usual Compose files. Check the
merged config, wait until no active jobs remain, then recreate the worker. Do not
point this at `/data/data` if that is MinIO's own storage directory.

Before rollout, verify with one real portal upload: job completion, JSON/PNG and
hash agreement, unchanged original bytes after success/failure/cancellation, no
download calls, and no duplicated original. Test portal retrieval and deletion
as well. This local implementation alone does not establish those portal results.

Without this overlay and with HEATPIPE_SHARED_INPUT_ROOT empty, the existing
download path is used. Rollback requires restoring an empty value and recreating
the worker while idle, with valid source URLs still available.
