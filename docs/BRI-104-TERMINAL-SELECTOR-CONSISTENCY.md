# BRI-104: terminal selector consistency and bounded offline 504 diagnosis

## Scope and result

Canonical repository: crystegg32/ChatGPT-Web2API. This is standalone Bridge
correctness work, separate from Reviewer protocol/packaging and DSH. Base:
`23f165e9ba200df7735997c48427775b783414a8`. No runtime deployment or account
request is part of this checkpoint.

The historical second-turn HTTP 504 remains **unknown / BLOCKED**. There is
neither reliable evidence that the model remained incomplete nor an exact
completed assistant/parent-chain/end_turn/text proof for that submitted turn.
An independently discovered deterministic selector defect is fixed below;
the historical projection was not retained, so causation is unproven.

## Existing evidence timeline (local clock, UTC+8)

Only existing durable state, logs and exact source were read. Identities are
represented by aliases here; their original UUIDs remain in private evidence.

| Event | Time | Safe evidence |
| --- | --- | --- |
| First request | 22:20:06.672 | 17,545 input characters/UTF-8 bytes |
| First baseline | 22:20:11.827 | 0 assistant / 0 user DOM nodes |
| First identity / click | 22:20:12.853 / .854 | captured user U1 |
| Second request | 22:21:43.238 | same conversation C; 26,072 characters / 26,132 bytes |
| Conversation ready | 22:21:47.003 | conversation C |
| Second baseline | 22:21:48.019 | 0 assistant / 0 user DOM nodes |
| Second identity / click | 22:21:49.538 / .539 | new captured user U2; capture success |
| Failure | 22:23:20.680 | phase_1_appear; approximately 91 seconds without DOM progress |

Input.insertText responses arrived in approximately 174.5 ms / 67.8 ms,
respectively, with zero pending commands and reader not done/cancelled.
There is no insert timeout evidence here. UUID capture and click do not prove
backend acceptance or final completion. The first response parsed as a batch;
four local operations ran, with feedback delivery remaining submission_unknown.

Phase 1 backend recovery was eligible from the captured identity. Trace was
disabled, so actual raw/collapsed projection statuses, selected assistant and
parent-chain/end_turn results are absent. Exact anchor mode was not logged;
an existing conversation can use existing_conversation or degraded_existing.
The mode therefore remains unknown.
Phase 2, `_reconcile_before_stall()` and the driver final-text tail were not
reached. Zero DOM baseline despite a prior parsed response is a visibility or
selector-risk indicator, not proof of a particular failure mechanism.

## Reproducible defect and minimal fix

For one exact captured user with two completed, nonempty text descendants,
the end selector chose the first descendant while the text selector chose
the newest create_time. Shared projection verification correctly rejected
the differing assistant identities; recovery could therefore false-stall.

The end selector now uses the same maximum create_time rule. Equal timestamp
ties preserve existing traversal order, matching the text selector. No
identity, parent-chain, end_turn, nonempty-text, nontext guard or DOM fallback
is relaxed. No extra fetch, resend, retry or timeout change is added.

The regression uses the real BackendClient selection and Phase 1 handoff over
mocked projections: two child orders and unequal/equal timestamps. Before the
fix: 1 failed / 3 passed. After the fix: all four pass. Existing negative tests
retain old/wrong turn, wrong assistant, broken parent, unfinished/empty text,
conversation mismatch, 401 and 429 rejection.

## Verification checkpoint

- Windows, Python 3.13.11, exact candidate source via process-local PYTHONPATH.
- Focused: 85 passed in 0.47 s.
- Full offline suite: 822 passed in 300.39 s; 31 live E2E tests deselected.
- Independent review: PASS; independent focused execution: 65 passed in 0.34 s.
- `git diff --check`: PASS. Ruff src/tests: 29 existing findings in both exact
  base and candidate, no added findings. Existing lint debt is not repaired here.
- GitHub Actions workflow registry: 0. Workflow file only targets master;
  the separate stacked Draft PR does not establish formal CI PASS.
- Failed durable state hash matches the original acceptance evidence. No live
  observation, prompt, deployment, merge or historical state write occurred.

Offline command (using an installed development environment):
`PYTHONPATH=src python -m pytest -m 'not e2e' -q --tb=short`.

## Missing proof and next acceptance gate

The smallest missing evidence is safe metadata from observations already
performed: live conversation binding, raw/collapsed selector status, selected
user/assistant identity, parent correlation, end_turn and final text format/hash.
Transport response success alone cannot establish those facts. The existing
completion trace flag is not purely passive: its stall helper performs another
projection fetch, so enabling it must not be described as GET-free observation.

A further blind retry on unchanged runtime has low diagnostic value. Any later
controlled acceptance needs reviewed deployment approval and sufficient bounded
observation evidence; this checkpoint does not authorize another real review.

GitHub exact-SHA native review avoids Web UI DOM/completion transport dependence
and improves source pinning. It still requires an independently designed evidence
coverage/structured verdict contract and cannot silently substitute for the
current local allowlisted gateway or claim the existing acceptance passed.

## Separate operations proposal (not implemented)

Desktop starts its installed stdio Reviewer; the verified Bridge/Chrome launcher
is a separate manual process and is not part of that lifecycle. This explains
why Desktop restart alone does not restore Bridge. Bounded issue searches found
no dedicated Bridge autostart issue; operational work stays outside this PR.

A small future operations task can register one user-login startup action for
the pinned launcher, preserving profile/config, checking existing listeners to
avoid duplicate processes, and writing a local readiness result. It must not
send prompts, retry reviews or add an automatic restart loop. Startup task
installation and any additional OS permission require separate authorization.
