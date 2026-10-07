"""No assistant DOM nodes, but exact captured turn has completed in backend."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatgpt_web2api.cdp_driver import AuthExpiredError, GenerationStuckError
from chatgpt_web2api.completion_detector import CompletionDetector
from chatgpt_web2api.turn_anchor import TurnAnchor, TurnEndResult, TurnTextResult


def setup(monkeypatch, *, captured="new-user", end_status="matched", text_status="matched",
          end_user="new-user", text_user="new-user", end_assistant="new-assistant",
          text_assistant="new-assistant", conversation="conv", expected_conversation="conv", text="new reply"):
    clock = [0.0]
    monkeypatch.setattr("chatgpt_web2api.completion_detector.time.monotonic", lambda: clock[0])

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr("chatgpt_web2api.completion_detector.asyncio.sleep", sleep)
    driver = MagicMock()
    driver._current_conv_id = expected_conversation
    driver._get_live_conversation_id_best_effort = AsyncMock(return_value=conversation)
    driver._fetch_end_turn_for_turn = AsyncMock(return_value=TurnEndResult(
        end_status, {"user_node": end_user, "assistant_node": end_assistant, "reason": "text_end_turn"}))
    driver._fetch_text_for_turn = AsyncMock(return_value=TurnTextResult(
        text_status, text, {"user_node": text_user, "assistant_node": text_assistant}))
    async def shared(*_):
        end = driver._fetch_end_turn_for_turn.return_value
        text_result = driver._fetch_text_for_turn.return_value
        if driver._fetch_end_turn_for_turn.side_effect:
            raise driver._fetch_end_turn_for_turn.side_effect
        return end, text_result
    driver._fetch_turn_results = AsyncMock(side_effect=shared)

    async def js(expression):
        if "body.innerText" in expression:
            return json.dumps({"text": "normal"})
        return "0"  # Both old and new assistant nodes are invisible to the legacy selector.

    driver._js_strict = AsyncMock(side_effect=js)
    anchor = TurnAnchor("prompt", "existing_conversation" if expected_conversation else "fresh_chat",
                        captured_user_message_id=captured, conversation_id_at_capture=expected_conversation)
    return CompletionDetector(driver), driver, anchor


async def collect(detector, anchor):
    return [chunk async for chunk in detector.stream_until_complete(
        initial_count=0, timeout=100, turn_anchor=anchor)]


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_conversation", ["conv", None])
async def test_completed_exact_turn_without_dom_nodes(monkeypatch, expected_conversation):
    detector, driver, anchor = setup(monkeypatch, expected_conversation=expected_conversation)
    chunks = await collect(detector, anchor)
    assert [chunk.delta for chunk in chunks] == ["new reply"]
    assert detector.last_dom_text == "new reply"
    assert not detector.had_non_text_content
    driver._fetch_turn_results.assert_awaited_once_with("conv", anchor)
    driver._fetch_end_turn_for_turn.assert_not_awaited()
    driver._fetch_text_for_turn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["not_ready", "ambiguous", "degraded_not_fresh", "fetch_failed", "non_text"])
async def test_unresolved_backend_never_unlocks_dom_fallback(monkeypatch, status):
    detector, driver, anchor = setup(monkeypatch, end_status=status)
    with pytest.raises(GenerationStuckError) as caught:
        await collect(detector, anchor)
    assert caught.value.phase == "phase_1_appear"
    driver._fetch_text_for_turn.assert_not_awaited()
    assert detector.last_dom_text == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"captured": None}, {"end_user": "previous-user"}, {"text_user": "previous-user"},
    {"text_assistant": "previous-assistant"}, {"end_assistant": None},
    {"conversation": "other-conversation"}, {"conversation": ""}, {"text": ""},
    {"text": "  "}, {"text_status": "not_ready"}, {"text_status": "ambiguous"},
])
async def test_stale_wrong_or_unproven_identity_fails_closed(monkeypatch, changes):
    detector, driver, anchor = setup(monkeypatch, **changes)
    with pytest.raises(GenerationStuckError):
        await collect(detector, anchor)
    assert detector.last_dom_text == ""
    if changes.get("captured", "present") is None or "conversation" in changes:
        driver._fetch_turn_results.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_expiry_propagates_without_recovery(monkeypatch):
    detector, driver, anchor = setup(monkeypatch)
    driver._fetch_end_turn_for_turn.side_effect = AuthExpiredError("expired")
    with pytest.raises(AuthExpiredError):
        await collect(detector, anchor)
    driver._fetch_text_for_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_exception_does_not_return_old_text(monkeypatch):
    detector, driver, anchor = setup(monkeypatch)
    driver._fetch_end_turn_for_turn.side_effect = RuntimeError("offline")
    with pytest.raises(GenerationStuckError):
        await collect(detector, anchor)
    driver._fetch_text_for_turn.assert_not_awaited()
