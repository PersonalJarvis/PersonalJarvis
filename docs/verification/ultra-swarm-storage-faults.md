# Distributed storage process-fault qualification

Date: 2026-09-19. Candidate: `9e99a8cceee088915815b2f7970874849ca57ad4`.
Tier: T3 distributed storage contract. No production source or Git changes were made.

**Three unique fault/retention cases passed in independent test-runner executions.**
This closes the previously missing literal Redis restart, complete S3 service
outage/recovery, and actual operator-triggered S3 lifecycle execution evidence.
It does not qualify browser performance, automatic bucket policy provisioning,
scheduled background expiration, cloud IAM, or native installation.

## Topology and sample

All service ownership was checked against the earlier disposable-service handoff.
The harness also refuses unexpected container names, images or port bindings.
Only the existing named Redis and S3 containers were stopped and restarted.
Each process restart is attested by its changed Docker start timestamp, an actual
application connection failure while stopped, and successful application
connection recovery. All three owned services were running at the end.

| Service | Image | Loopback binding |
| --- | --- | --- |
| PostgreSQL | `postgres:17-alpine` | `127.0.0.1:55434` |
| Redis | `redis:alpine` | `127.0.0.1:56381` |
| S3-compatible object store | `chrislusf/seaweedfs:latest` (inspected version 4.46) | `127.0.0.1:58334` |

Immutable image IDs are recorded in [the JSON evidence](ultra-swarm-storage-faults.json). This is one Windows Python
process talking to three Docker Desktop Linux services. Each fault case used two
teams and two task records. The retention case used two teams and two small
objects. There were no model calls. Task/verifier inputs and the application
lease/outbox clock are deterministic fixtures; storage I/O, process faults,
provider lifecycle execution and measured elapsed time are real.

## Redis process restart

Test: `test_literal_redis_restart_preserves_pg_results_and_acknowledgements`.

- A task result committed to PostgreSQL while Redis was stopped. Its evidence had
  already been written to real S3 storage.
- A consumer acknowledgement committed to PostgreSQL while its Redis
  acknowledgement failed. That durable acknowledgement survived restart.
- Seven unacknowledged durable events were recovered through the actual outbox
  and Redis Streams. An accepted-result replay retained exactly one verification,
  one evidence artifact and one reputation record.
- Forged references to another team's event and duplicate acknowledged references
  were filtered; cross-team task access and acknowledgement were rejected.
- Explicit retention pruned all eight acknowledged outbox rows while preserving
  the accepted task and durable event history.
- Stop-command-through-healthy interval: **8.328 seconds**, including a **0.844
  second** stop command and **1.203 seconds** from restart request to healthy.

Redis append-only persistence stayed enabled. This is a literal process restart,
not a flush. Lost-stream recovery remains covered by the separate existing
distributed contract rather than being conflated with this sample.

## S3 process outage and recovery

Test: `test_literal_s3_outage_keeps_unknown_quota_and_recovers_one_artifact`.

- Existing artifact reads and a new write failed while the actual S3 service was
  stopped. The failed write retained a three-byte unknown-upload reservation;
  another write could not exceed the six-byte team quota.
- PostgreSQL remained usable: one measured heartbeat plus checkpoint took
  **31 milliseconds** during the S3 outage. This is one observation, not a load SLO.
- Retrying the original operation key after restart produced one new artifact.
  Replaying it preserved its identity. The final ledger contained exactly two
  artifacts and six bytes, with no pending upload reservation.
- Original content, recovered content and another team's original content were
  verified through real S3 reads. Cross-team reads were rejected during outage.
  Task-result replay produced one committed result and one reputation record.
- Stop-command-through-healthy interval: **17.797 seconds**, including a **2.688
  second** stop command and **1.734 seconds** from restart request to healthy.

## Actual scoped lifecycle execution

Test: `test_real_s3_lifecycle_expires_only_deleted_team_prefix`.

The test created a unique bucket and two team namespaces. Explicit application
retention preserved referenced evidence. Team deletion committed its PostgreSQL
tombstone and reported `retained_for_bucket_lifecycle`; object bytes were still
readable directly from the operator's S3 client immediately afterward. The
application could no longer open the deleted team, and deletion replay kept one
tombstone.

Only then did the test operator install an already-due absolute expiration rule
for that deleted team's exact key prefix. It checked that no unrelated bucket
had an enabled rule before invoking a bounded pass of the actual SeaweedFS
lifecycle worker. One deleted-team object expired; an exact S3 listing contained
only the live team's key. The live team's content and running task survived.
The worker pass and result verification took **0.453 seconds**.

The provider's [pinned lifecycle command implementation](https://raw.githubusercontent.com/seaweedfs/seaweedfs/d997fba1575583a89cf0cc50dc0150642286c86d/weed/shell/command_s3_lifecycle_run_shard.go)
executes its normal lifecycle engine against the live filer and S3 server. This
test explicitly invokes that engine; it does **not** prove a configured daily
scheduler. The runtime's gRPC listener is `18333`; its HTTP listener is `8333`.
Object absence is verified, but physical disk compaction and billing reclamation
are not measured. The unique test bucket and its namespaces were removed afterward.

## Evidence, failures and final source

Owned test source: `tests/contract/test_swarm_distributed_faults.py`, SHA-256
`2cb0f6d1c47bf694e7a95373455fe27bc944c005278994d71066aade794ef30a`.
Ruff check and format check passed.

| Execution | Result | Preserved evidence |
| --- | --- | --- |
| Initial mixed native/fault selection | 49 passed, one lifecycle helper failure; both literal outage cases passed | `../swarm-native-and-fault-tests.log` and `.xml`; `run-1/*.json` |
| Corrected lifecycle gRPC endpoint | One passed, 6.20 seconds | `run-2/lifecycle-only-tests.log` and `.xml` |
| Final exact remaining-object listing assertion | One passed, 5.35 seconds | `run-3/lifecycle-only-tests.log` and `.xml`; final lifecycle JSON |

The initial helper used the provider help's HTTP-port example for a gRPC call and
failed negotiation. The source/port correction and failed evidence are retained;
this was not a production defect. The final additional listing assertion removes
ambiguity between object absence and a domain read error. Only that changed case
was rerun; the Redis/S3 cases were unchanged. Counts above are separate execution
sets and must not be summed into a unique-test total.

[the JSON evidence](ultra-swarm-storage-faults.json) is the sanitized machine-readable summary. Detailed pytest logs/XML
are local evidence and may include local runtime paths or machine metadata. They
should not be published without separate sanitization.

## Acceptance boundary

- **I12 storage gap:** literal Redis process restart and full S3 service
  outage/recovery are now demonstrated without duplicated committed results,
  loss of the sampled durable events/artifacts, or the sampled cross-team access.
- **I09 retention gap:** real provider lifecycle execution for an explicitly
  configured deleted-team prefix is demonstrated. Automatic policy provisioning
  and scheduled background expiry remain outside this proof.
- **P-FAULT-SLO / V07 / V09:** this report covers storage behavior only. No browser,
  rendering, large-scale throughput, regional failover or cloud IAM result is
  inferred from these small samples.
- A literal PostgreSQL process stop was not added; the separate existing actual
  database-disconnect rollback contracts remain the relevant prior evidence.
