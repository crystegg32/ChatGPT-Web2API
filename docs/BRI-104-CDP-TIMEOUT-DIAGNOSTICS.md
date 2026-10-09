# BRI-104 CDP input-stage diagnostics

Base: exact `27c5773ed030433bb036e34094958bc97925e0f5`. This independent candidate does not change Reviewer protocol, input strategy, timeout defaults, identity/parent/end_turn checks, or final-text consistency.

## Evidence and classification

The BRI-106 batch acceptance parsed three batch responses and executed twelve local operations. Request four failed with a 15-second `Input.insertText` response timeout before the driver's click path. A later independent read-only `Runtime.evaluate` to the same target also timed out, before auth/projection GET. Page/renderer/target responsiveness is favored; the cause is unconfirmed. Larger input is a correlation, not a demonstrated size threshold. Fourth delivery remains unknown; no final verdict was recovered. No further runtime attempt is part of this candidate.

**Proven defect fixed:** caller cancellation during websocket send or response wait could leave an id/future in the pending map. A finally block now removes it and cancels an unfinished future. This does not establish that cancellation caused the historical renderer timeout.

**Risk reduction:** new `CDP_STAGE` events record command id, a fixed method category, stage, monotonic elapsed/send-complete times, timeout, pending count and reader done/cancelled flags. Insert events also record character/UTF-8 size; invalid Unicode has unknown (`null`) UTF-8 size and is never transformed. Response bodies become only `result` or `cdp_error`. Insert and failure/late events are INFO; ordinary commands are DEBUG. Up to 128 metadata-only observations correlate late responses, which are discarded rather than accepted as confirmation. No diagnostic GET, poll, reconnect or resend is added. The response deadline still starts after websocket send returns.

Request/Typed logging no longer emits input prefixes; composer verification errors no longer include expected input. No text, source, prompt, credentials, headers or raw CDP response is added to diagnostics. Existing unrelated logs are not claimed to be comprehensively sanitized.

## Offline batch cap study

Reviewer default stays **64 KiB**. No Reviewer code or deployment changes. The opt-in offline script reconstructs bounded successful reads using the immutable workspace and durable trace, groups by `(call, step)`, and emits sizes/counts only. Search queries/results were not persisted and are not reconstructed. Original operation IDs are unavailable; 32-character IDs give a conservative upper bound. This is a finite sample, not measured model behavior or a historical wire replay.

Known four-read envelopes: first batch <=25,899 bytes; third batch <=31,251 bytes (Reviewer JSON envelope, excluding Bridge `[User]` wrapper).

| Hypothetical cap | Direct admission of known batches | If planned as whole-operation smaller batches |
| --- | --- | --- |
| 16 KiB | Both terminally rejected; no evidence payload delivered | 2 + 3 groups, +3 evidence round trips |
| 24 KiB | Both terminally rejected; no evidence payload delivered | 2 + 2 groups, +2 evidence round trips |
| 32 KiB | Both admitted | 1 + 1 groups, no extra round trips |

Direct cap calculations include the existing 1-KiB-per-remaining-item reserve. Hypothetical presplitting estimates exclude reserve/model behavior and assume the unknown middle batch is unchanged. Known whole operations fit individually, so presplitting would not increase those eight operation attempts or truncate data. Splitting line ranges could increase operation counts, but is not needed for these known items and is not implemented. The other batch includes three unknown searches and one reconstructed read; whole-review round trips/latency cannot be predicted. Trace truncation is explicitly reported.

Lowering only the cap would terminate earlier, not automatically replan, retry, or silently truncate. 32 KiB admits the very payload associated with the failure and is not a demonstrated fix. Keep the production default until a separate design/controlled experiment is authorized.

## Validation and next controlled acceptance

Regression covers exact response-id routing, timeout and late response, bounded metadata, cancellation during send/wait, safe logging for large/invalid Unicode, call-scoped study grouping, and real driver/DOM typing timeout preventing verification retry, click_send and completion. Existing send/identity and final-text safety tests remain required.

Local focused: **70 PASS**; full suite: **818 PASS** in 298.94 seconds, 31 real-account E2E tests deselected, on Windows / Python 3.13.11. Independent follow-up review: **PASS**, independently ran the 10 new tests. Offline sdist/wheel build and isolated wheel import/CLI-help smoke: **PASS** (MCP 1.30.0); all 30 packaged Python files match candidate source. Gitleaks 8.21.2 snapshot: no findings. Ruff base/candidate both have the same existing 29 diagnostics; no new lint regression. These are offline results, not formal CI or runtime acceptance. GitHub's available workflow registry remained empty; existing workflow triggers also target master rather than this stacked base. Do not claim a remote test/build/secret/lint job ran when no run exists.

Deploy only with explicit authorization after review. The next controlled experiment should load the diagnostic candidate, verify exact runtime/readiness, then make one explicitly authorized fresh bounded batch review. Capture safe stage events and stop at timeout/429/invalid_protocol/text_mismatch/uncertain delivery. Do not resend or infer absence of delivery from missing identity capture. Root cause and structured-final acceptance remain open.
