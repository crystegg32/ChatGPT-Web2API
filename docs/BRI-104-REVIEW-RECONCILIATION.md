# BRI-104 failed review reconciliation checkpoint

Date: 2026-10-07 (Asia/Taipei). Bridge implementation checkpoint: `aaf49553b6cb4833074d1d15e6ec94c0c44e60cf`.
Reviewer checkpoint: `d1756aa18fe4717455e2682046f92a4b7fa873ba`.
Issue: [BRI-104](https://linear.app/brian-dev-hub/issue/BRI-104).
PR: [Bridge PR #1](https://github.com/crystegg32/ChatGPT-Web2API/pull/1).
This checkpoint adds diagnostic documentation only. It does not change Bridge,
Reviewer, packaging, durable state, or runtime policy.

## Result

Review `review-ae6909115a32` remains **BLOCKED** for code-review acceptance;
its durable status remains `failed`. No structured final verdict was recovered.
The latest completed assistant action is `search`, not `final`.

Bridge was restored from the checkpoint above using the existing dedicated
Chrome profile. Read-only doctor confirmed health/model/project access, connected
Chrome/CDP/driver, and `read_surfaces_ready=true`. Health still reported `starting`
before any chat request, consistent with existing health semantics. This readiness
check did not send a turn and did not prove chat completion correctness.

## Existing review evidence

The later failed review used one conversation and six captured submitted user
identities. A single GET-only conversation projection observation verified each
captured identity and its completed assistant action. The latest current assistant
had the exact submitted user ancestor, consistent parent chain, assistant identity,
nonempty text and `end_turn=true`. No new prompt, review, continuation, navigation,
or uncertain-turn resend was performed during reconciliation. No 429 was observed.

Recovered protocol sequence:

| Step | Completed assistant action |
| --- | --- |
| 1 | Read `docs/architecture.md`, lines 1–220 |
| 2 | Read `docs/BRIDGE-OBSERVATION-RELIABILITY.md`, lines 1–220 |
| 3 | Read `src/chatgpt_web2api/backend_projection.py`, lines 1–220 |
| 4 | Read `src/chatgpt_web2api/projection_diagnostics.py`, lines 1–220 |
| 5 | Read `src/chatgpt_web2api/backend_client.py`, lines 1–260 |
| 6 | Search with path `src/chatgpt_web2api/backend_client.py` |

The first five read requests correspond to the durable evidence paths; their
gateway results were submitted in subsequent turns. The sixth assistant response
was complete and structurally valid, but its search path names a file.

## Failure classification and confidence

Reviewer `search_text` at the recorded checkpoint requires a directory and rejects
this exact file path before traversal with:

```text
WorkspaceAccessError: search path is not a directory: src/chatgpt_web2api/backend_client.py
```

This was reproduced offline using the unchanged Reviewer gateway. Therefore the
evidence supports failure at the **local search gateway after step 6**, rather than
a missing assistant completion or observation 429. The original terminal exception
was not persisted in durable state or the available Bridge log, so this classification
is reconstructed from the exact verified assistant action and deterministic gateway
behavior. Completion/projection tracing was not enabled for the failed run; detailed
fetch timing and completion-phase traces cannot be reconstructed from these logs.

The failure does not establish a Bridge implementation defect or PASS. Independent
review of the Bridge candidate remains incomplete. No new review or continuation
is authorized by this documentation checkpoint.

## Evidence handling and validation

The local reconciliation report retains identity proof; the committed summary
omits conversation/message identifiers, source contents, prompts, cookies,
Authorization, project catalog, private machine paths and state payloads. Durable
state was checked byte-for-byte unchanged. BRI-98 Reviewer PR #3 remains separate.

Existing implementation evidence remains 109 focused / 792 full-suite PASS.
No functional files changed after that suite; tests were not rerun for this
documentation-only checkpoint. The previously observed upstream 429 cause and
threshold remain unproven.
