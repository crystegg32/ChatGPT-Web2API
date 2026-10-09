# Offline final-text reconciliation candidate

Base: exact `cf6058482c812e4b7112dae6a2cd42d5e160cf8c`.
Isolated branch/worktree: `codex/bri-104-final-text-consistency`.

The previous final handoff appended backend text by length alone. If a DOM
snapshot is not a prefix of backend final text, that splice can manufacture
invalid content even when the backend contains valid JSON. DOM revisions during
streaming can also make emitted chunks differ from the final DOM snapshot.

The driver now tracks its actual emitted aggregate. Only after the existing
turn-anchored selector returns matched final text does it check that BOTH the
last DOM snapshot and emitted aggregate are exact prefixes of backend final text.
Exact equality emits no duplicate text. A valid prefix emits only the missing
suffix based on the emitted aggregate's length. Empty streamed content can emit
the full already-verified backend final. No normalization or JSON repair occurs.

Any mismatch immediately raises existing TurnReconciliationError with
last_status=text_mismatch, a fixed reason and lengths only. No raw content is
included in diagnostics, no suffix is appended, and capture scope is closed by
the existing finally block. There is no extra fetch, poll, send or retry.
Identity/end_turn/parent-chain selection remains unchanged and runs first.

Non-streaming collection propagates failure to HTTP 500 instead of returning
successful corrupted content. Streaming cannot retract partial chunks already
delivered: it emits the existing SSE error contract (finish_reason=error), never
normal stop; [DONE] still terminates transport and is not a success verdict.
Clients must discard partial content on error. The error is not a rate-limit
exception and does not enter the existing send retry handler.

Offline tests exercise the actual driver with fake completion/CDP boundaries,
and real full-response/SSE formatter control flow with an in-memory response.
They cover equality, prefixes, empty DOM, Unicode, same-length/shorter/longer
divergence, snapshot/emitted disagreement, and append-only corruption. Existing
turn-anchor and completion regressions also run. No live account or HTTP client
was used; no deployment, merge or new review occurred. This addresses an offline
correctness defect, not proof of the historical BRI-106 json_syntax root cause.

Validation (2026-10-09):
- Focused driver/turn-anchor/SSE/projection/non-text regression: 85 passed (0.82s).
- Full offline suite: 808 passed (304.91s); 31 opt-in live E2E tests deselected.
- Independent static code review: PASS, no actionable blocker. Independent
  runtime probes did not complete and are not counted as passing evidence.
- git diff --check: PASS.
- Original Bridge checkout remains clean; the historical BRI-106 failed review
  state remains unchanged. Reviewer diagnostic checkpoint 40156c2 is not installed.

Release gate follow-up:
- Formatted only the new test file; rerun focused regression: 85 passed (0.59s).
- Ruff on the changed product/test files: PASS. Full repository Ruff: 29 existing
  findings on both base cf60584 and this candidate; no unrelated lint repairs.
- Existing CI only triggers for master-base PRs; this stacked PR preserves its
  codex/bridge-observation-diagnostics base. An attempted base-filter-only CI
  change could not be pushed because the GitHub credential lacks workflow scope;
  that workflow change was removed before publishing. No existing PR base moves.
- Full repository lint is a release blocker. No deployment or live review may
  proceed while CI is failing; Reviewer 40156c2 remains uninstalled.
