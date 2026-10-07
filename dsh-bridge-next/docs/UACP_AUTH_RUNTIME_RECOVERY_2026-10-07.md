# UACP / AA / DSH auth + runtime recovery — 2026-10-07

Status: PASS / DEPLOYED / LIVE RECEIPT VERIFIED

## Scope

Transport-only recovery for Academic Paper Workbench. No PaperWB science, Writing Foundation route, preregistration, blind-review or scoring logic was modified.

## User-auth incident

The native DSH account user access token had expired normally after the Agents Anywhere server's seven-day user-token lifetime. Desktop/native OAuth stores `accessToken` + `expiresAt` and has no desktop refresh-token grant. Connector authentication is a separate credential domain and remained online through the connector token exchange path.

The user completed the official Agents Anywhere login/OAuth flow. The native account file was rewritten by the official plugin flow. Live `/auth/me` validation on the Mac returned valid and not disabled.

## Connector lifecycle recovery

After re-login, the plugin reported another Connector was running. The only competing process was the previously deployed standalone CLI Connector using the same saved binding:

- connector id: `conn_eXJuYKTK2278qg`;
- no duplicate device/binding was created.

The old standalone process was stopped. The DSH plugin then reclaimed the stale runtime owner and started the same connector id as a plugin-owned RPC child. Runtime ownership changed from `kind=cli` to `kind=dsh-plugin` while preserving the connector identity.

## DSH runtime blocker

After auth recovery, a fresh UACP smoke reached AA but failed before model execution because runtime instance `rti_Bt7mOxeZ7AUdE1kv` remained `starting`.

Root cause: startup session inventory is event-synchronized and runtime health becomes `running` only after `session.inventory.complete`. `SyncFeed` used a fixed 60-second ACK timeout. Production startup ACKs were observed taking approximately 30–43 seconds and historical streams repeatedly crossed the 60-second boundary, causing false sync failures/resubscriptions and leaving the runtime in `starting`.

## Fix

Commit:

`7cf11357fee1facd761f347d4ddaf2aca946fe24`

Changes:

- define `SYNC_ACK_TIMEOUT_MS = 180_000`;
- use that bounded budget for DSH event/inventory sync ACKs;
- keep ordinary model/session RPC timeout behavior unchanged;
- preserve bounded failure behavior for genuinely missing ACKs;
- add regression coverage for the production ACK budget.

## Tests

- targeted runtime-recovery tests: 5/5 PASS;
- typecheck: PASS;
- build: PASS;
- bundled Connector build: PASS;
- check-build: PASS;
- full unit/integration suite after dependency warm-up: 175/175 PASS.

During the first full-suite attempt, Python integration probes exposed a local test-environment issue: the installed optional `@dataiku/uv-darwin-arm64/bin/uv` existed without executable mode, so direct `tsx` test invocation fell back to user `uv` while child probes lacked that PATH. The binary executable mode/test PATH were corrected without product-source changes. Two first-run probes then exceeded their cold dependency-download timeouts; both passed immediately after the dependency cache was populated. A later full-suite run had one random manager-lock port collision with the simultaneously running production plugin; that test passed in isolation. The final full suite then passed 175/175.

## Deployment

The `uacp-aa-web` DSH profile links directly to this checkout. After building the fixed host, the old DSH/connector processes were stopped and the same profile restarted.

Post-deploy state:

- DSH process restarted from the linked checkout;
- connector id unchanged: `conn_eXJuYKTK2278qg`;
- connector owner kind: `dsh-plugin`;
- no duplicate device or binding;
- startup sync completed successfully;
- `sync.inventory_completed` reported 92 sessions;
- no new sync failure occurred in the deployed startup stream.

## Live UACP attachment/receipt smoke

Fresh run root:

`.uacp-runtime/aa-auth-recovery-smoke-20261007-b`

Fresh AA session:

`sess_yLoxbEB2FR2ESA`

Expected nonce:

`UACP_AA_AUTH_RECOVERY_OK_20261007_B`

A PowerShell wrapper treated the worker's diagnostic `AA_SESSION_ID=...` on stderr as a native-command error before it wrote its convenience stdout file. The session itself completed successfully; therefore no duplicate session was created. The write-once receipt was read instead.

Receipt evidence:

- schema: `uacp.aa-dsh-execution-receipt.v1`;
- source: `AA_RUNTIME_STATE_API`;
- catalog resolution: `resolved`;
- provider/model/reasoning: `deepseek-account / deepseek-flash / high`;
- permission: `read-only`;
- input manifest transported: `true`;
- final runtime state: `idle`;
- final assistant text SHA-256 exactly matched the locally computed SHA-256 of the expected nonce.

Therefore the final assistant output was deterministically verified without opening another generation.

Disposition:

`AA_DSH_TRANSPORT_RECOVERY=PASS`

`SAFE_TO_RESUME_PAPERWB_B0=YES`
