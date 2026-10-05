# BRI-92 send-confirmation diagnosis (2026-10-05, Asia/Taipei)

The valid bridge base is `0cf9af2dac662f95c8a866197bed27c950ffa3e0`;
its identity-safe Phase 1 completion recovery is retained. Reviewer checkpoint
is `f683c0050c477b7287aeab9b2387896ea595fe66`.

## Diagnostic-only reproduction

A new independent diagnostic review reproduced HTTP 500 at 19:34:41 before
completion detection. No uncertain turn was resent. Conversation:
`6ac38b25-b848-83ec-9459-744a1f1c4b69`.

| Evidence | Observation |
| --- | --- |
| Pre-send DOM user count / latest identity | 0 / null (selector drift) |
| Pre-send backend count / latest identity | 2 / `48141c9d-5313-46c8-b88e-d4fc1fab2af3` |
| Composer before click | Present; 45,397 visible characters; upstream recursive verification accepted the intended text |
| Send candidate | Broad form submit selector; enabled; label `傳送` |
| `click_send` | Python return `None`; underlying JS dispatch result `sent` |
| Post-click composer | Empty (placeholder newline only) |
| Post-click DOM user count | Still 0 |
| Identity capture | No accepted UUID; 5-second capture timeout |
| Conversation | Existing real conversation ID; unchanged URL |
| Backend after capture timeout | 3 users; new identity `d90ebb18-127b-4a4f-8a24-51541951a9d6` |
| Final old confirmation condition | DOM count delta failed during its 3-second budget; composer actually **was** empty |

The message was delivered. The original exception's fixed wording incorrectly
claimed the composer was not cleared. The accepted new backend user turn's
full text was 46,652 characters versus 45,394 expected raw characters. First
difference: a generated backslash before `*`. Full comparison proved the only
differences were inserted CommonMark punctuation escapes and generated URL
autolinks with identical label/destination. Raw SHA-256 capture validation
rejected this serialized representation. This is an observed serialization
false negative, not evidence that backend propagation alone caused the failure.

The capture waiter also used unshielded `asyncio.wait_for`: timeout cancelled
the capture future and prevented any late observation from being consumed.

## Correction and safety

- Keep the raw hash fast path. For a hash mismatch, accept only **full** text
  correspondence under the observed reversible serialization. Preserve all
  original characters/backslashes and whitespace; no prefix or fuzzy match.
  Changed link destinations, missing suffixes and unrelated text are rejected.
- Shield the per-send capture future so an initial timeout leaves it armed.
  The existing finally block still closes it on every terminal path.
- If the old DOM confirmation explicitly fails, reconcile for a bounded
  15-second budget. A late validated scoped UUID is delivery evidence.
- Backend-only reconciliation requires the same existing conversation,
  a trusted pre-send set of user IDs, exactly one new full-text matching user
  node not in that set, a create time no earlier than the baseline, and an
  independently observed empty composer. The resolved backend message ID is
  passed to the unchanged identity-safe completion detector.
- Fresh/degraded baselines, duplicate matches, wrong conversation, stale IDs,
  transport failures or uncleared/missing composer never confirm delivery.
  Authentication failure propagates. Reconciliation performs no typing,
  clicking, navigation or resend. Polling waits for evidence, not a fixed
  unconditional sleep. Deadline exhaustion remains a typed failure.
- `W2A_SEND_TRACE=1` records metadata only. Earlier diagnostic `composer_matches`
  used `innerText` and reported false even on accepted input; it now calls the
  same recursive canonical verifier as actual typing. This diagnostic fix does
  not change composer insertion semantics.

Local evidence is retained in `X:/dev/output/bri92-runtime/`, including
`bridge-send-diag.stderr.log`, `send-diag-review.json` and
`send-text-comparison.json`. Authentication/profile data is not published.

## Validation

Focused identity/confirmation suite: 47 passed, including 29 new cases covering
full-text serialization, wrong destinations, literal backslashes, stale/duplicate
nodes, propagation delay, late UUID, missing composer proof, auth expiry and
transport failures.

- Bridge full suite: **741 passed** in 295.65 seconds (same default upstream
  account-E2E marker exclusions as the previous 712-test checkpoint).
- Reviewer full suite: **35 passed** in 16.67 seconds.
- Ruff, compileall and diff whitespace checks passed.
- Real doctor: ready, no failures; the first doctor invocation overlapped
  service startup and was repeated only after startup completed.
- Real search → read → final: `review-34b9a49fe816`, conversation
  `6ac38ed7-19cc-83ec-943b-ae48123c7086`, three steps, `revise` for the deliberately
  defective fixture.
- New reviewer OS process: same review/conversation, fresh read → final,
  two steps, `revise`. No uncertain message was resent.

The committed checkpoint review is recorded separately in BRI-92 after this
commit is exported and reviewed. No merge is requested.

## Checkpoint review follow-up

Real WebGPT reviewed the entire 846-line reviewer diff and 901-line bridge diff,
then read the committed identity-listener source. Review `review-8fcfe84281d4`,
conversation `6ac3902a-4cd8-83e8-b9c3-dfd4c183b730`, completed in five steps with
`revise`: a POST with empty/missing/non-string-only `content.parts` could bypass
the old conditional text validation and resolve the scope using UUID alone.

That finding is fixed by rejecting missing submitted text before either text
validation path or scope resolution. Five additional regressions prove these
POSTs leave the capture unresolved. The guard does not resend anything.

- Updated bridge full suite: **746 passed** in 296.71 seconds.
- Updated reviewer full suite: **35 passed** in 16.21 seconds.
- Updated focused identity/reconciliation suite: **48 passed**.
- Doctor and a new real search → read → final succeeded after the guard.
- The additional restart continuation captured its submitted UUID but backend
  projection became HTTP 429, then bounded Phase 1 ended in HTTP 504. This is
  retained as a failure, not PASS. Earlier same-conversation continuation at the
  preceding checkpoint passed. No uncertain message was resent.

Final continuation/checkpoint re-review results are recorded in the BRI-92
checkpoint once the backend rate limit clears; the old completion recovery and
its safety gates remain unchanged.
