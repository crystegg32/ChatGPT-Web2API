# Offline Bridge observation reliability candidate

Known baselines: Reviewer `d1756aa18fe4717455e2682046f92a4b7fa873ba`;
Bridge `411dca621243c177e3b7ef2022b1ae3d8077fbab`. This work is isolated on
`codex/bridge-observation-diagnostics`. BRI-98 packaging and Reviewer core are
outside this change. No live-account test, service restart or account polling
is part of the offline validation.

Tracked independently as [BRI-104](https://linear.app/brian-dev-hub/issue/BRI-104/bridge-projection-observation-reliability錯誤來源保留與同-turn-讀取去重).
The PR targets the Bridge checkpoint branch at `411dca6`, keeping its diff separate
from earlier Bridge changes and Reviewer packaging PR #3.

## Minimal implementation

1. Failed projection reads retain only HTTP status, upstream Retry-After, and
   sanitized error code/type. Arbitrary bodies, messages, credentials, paths
   and conversation contents are not published. Seconds and HTTP-date headers
   become typed observation-error metadata. Missing/invalid headers remain a
   local 60-second fallback explicitly identified as `bridge_fallback`; they
   must never be described as an upstream cooldown. REST includes provenance
   in the error body and `X-Bridge-Retry-After-Source` header.
2. Phase 1 completion and text selectors share one newly fetched projection.
   Success requires exact captured user and selected assistant identities,
   a proven parent chain back to that user, end_turn=true, and nonempty text.
   A one-use result handoff is scoped to the exact conversation and immutable
   anchor, reset on every completion call, and consumed by driver final
   reconciliation. This removes the Phase 1 success path's extra text and
   driver-tail projection fetches. It is not a cross-turn cache or a global
   rate limiter. Phase 2 and unresolved driver-tail reads retain their existing
   behavior. DOM fallback and prompt retry policy are unchanged.
3. Opt-in `W2A_PROJECTION_TRACE=1` emits only safe start/end observation markers:
   an opaque read id, known phase, timestamps and response status. The passive
   recorder correlates them with Network events; old/replayed/ambiguous markers
   yield explicit unknown provenance. Attribution also requires a matching
   Network initiator script tag; close timestamps alone are insufficient.
   Every matching GET records start, end
   (or null if unfinished), phase and response classification. 429 error bodies
   are read from existing CDP buffers only and reduced to allowed code/type.

## Affected files

- `backend_projection.py`: bounded error metadata and opt-in read markers.
- `projection_diagnostics.py`: Retry-After parsing, source labels and redaction.
- `backend_client.py`: exception metadata and shared-projection selectors.
- `completion_detector.py`, `cdp_driver.py`: exact-turn one-use handoff and phases.
- `api_server.py`: REST error provenance; no automatic send retry.
- `send_confirmation.py`: phase labels only; confirmation conditions unchanged.
- `scripts/observe_projection_reads.py`: strictly passive recorder.
- Projection, recorder and completion tests: offline guards and fetch counts.

## Offline validation

The focused tests execute projection JavaScript with a synthetic fetch response,
then verify seconds/HTTP-date/missing/invalid header handling, safe metadata,
single-fetch driver completion, old turns, wrong user/assistant ids, broken
parent chains, unfinished/blank responses, 401 and 429 propagation, no automatic
prompt resend, and passive recorder start/end/source/privacy behavior. The full
suite runs with E2E disabled. No upstream rate threshold or causal explanation
for a previously observed 429 is established by these tests.

Final offline results (2026-10-07): focused suite 109 passed; full Bridge suite
792 passed in 300.65 seconds, with live E2E excluded. `git diff --check` passed.
Reviewer was unchanged and its suite was not rerun. The candidate is undeployed;
both original working trees remain clean at the baselines above.

## Local code review checkpoint

The final diff review found no new scope or safety blocker: the shared projection
and one-use handoff keep exact user/assistant identities, a verified parent chain,
end_turn=true and nonempty text. Error exports are restricted to sanitized
code/type and parsed Retry-After provenance. Prompt resend and DOM fallback
conditions are unchanged. This is local code review, not an independent reviewer
or live WebGPT review. Existing SSE error shape is unchanged; retry provenance is
exposed in REST responses. Runtime evidence remains pending separate authorization.

## Deferred runtime validation

Requires separate user confirmation. Observe/reconcile the already submitted
turn first; do not substitute a new review or continuation. Use only the new
passive recorder, not the earlier local script with an optional GET probe.
Its only HTTP discovery is local CDP inventory; it never calls ChatGPT endpoints,
evaluates a fetch, navigates, types or sends. It runs once for a bounded window
(1–300 seconds), without a pressure test or continuous polling. Metadata with
unknown phases remains unknown. Marker tracing must be enabled in a separately
approved runtime before such a capture; none was enabled during this work.
