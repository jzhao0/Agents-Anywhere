# 2026-10-06 attachment download transient — diagnosis and bounded retry

Status: FIXED_IN_BRANCH / PRODUCTION_CONNECTOR_NOT_YET_RESTARTED

## Observed failure

A real UACP -> Agents Anywhere -> Mac connector -> DSH run failed before model execution while staging the first attachment for session `sess_8X8nf9ba-FHlSQ`.

The connector traceback shows the attachment GET failed inside httpx with:

`ConnectError(EndOfStream())`

The failed file was the first staged attachment and was approximately 11 KB. The failure occurred before DSH model generation entered the runtime.

The upper session API surfaced the connector RPC failure as HTTP 502. This 502 is an API wrapper for connector/runtime RPC failure; the traceback does not show the attachment content endpoint itself returning HTTP 502.

A later harmless live attachment smoke succeeded, and historical attachment E2E evidence also exists. There is no evidence from this incident of a persistent storage outage, proxy fault, attachment-size limit breach, hash mismatch, authentication failure, or DSH generation failure.

## Root cause classification

Most specific supported classification:

`TRANSIENT_CONNECTOR_HTTP_TRANSPORT_EOF`

The evidence supports a transient HTTPS transport failure between the connector and the Agents Anywhere attachment endpoint. The evidence does not support claiming an S3 visibility race or multi-instance storage routing defect.

## Fix

`connector.server.transfers.download_attachment()` now retries only `httpx.RequestError` for the idempotent attachment GET.

Policy:

- maximum 3 transport attempts per download phase;
- backoff: 0.1s then 0.2s;
- retry occurs inside the existing AA session before runtime/model execution;
- HTTP responses are not converted into retryable transport failures;
- existing 401 token-refresh behavior is preserved;
- a second network retry budget is available after an actual 401 refresh;
- final transport failure remains an explicit `ConnectorNetworkError`;
- no new AA session, DSH run, reroute, or hidden generation is created by this retry.

## Tests

Connector targeted suite using the production Python 3.12 connector environment:

- `connector/tests/test_server_transfers.py`
- `connector/tests/test_dsh_attachments.py`
- `connector/tests/test_connector_runtime_host.py`
- `connector/tests/test_connector_server_rpc.py`
- `connector/tests/test_runtime_instance_binding.py`

Result: `42 passed`.

The retry-specific test proves two initial ConnectError failures can recover on the third GET. The persistent-failure test proves the retry budget stops after exactly 3 GET attempts.

## Real read-only smoke

A real AA attachment endpoint was tested without creating a new session or starting DSH. The smoke reused a previously successful harmless attachment and wrapped the client so the first GET raised a synthetic `ConnectError`; the second GET used the real AA endpoint.

Result:

- `LIVE_RETRY_SMOKE=PASS`
- `HTTP_GET_ATTEMPTS=2`
- downloaded bytes: 47
- returned filename: `paperwb_input_0001.txt`

No token was printed and no session/model execution was created.

## Deployment boundary

The currently running production connector was intentionally not restarted while the formal UACP 8-hour passive soak is active. Therefore this branch is code/test/live-read verified but not yet deployed into the running connector process.

After the passive soak is accepted:

1. land/cherry-pick this fix into the connector source used by the running bridge;
2. rebuild `dsh-bridge-next` so the bundled connector contains the canonical connector fix;
3. restart the connector once;
4. run one brand-new harmless UACP AA/DSH attachment session smoke;
5. only then authorize the formal PaperWB B0 route to resume.
