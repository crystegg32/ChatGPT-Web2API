import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatgpt_web2api.api_server import APIServer
from chatgpt_web2api.cdp_driver import CDPDriver, StreamChunk
from chatgpt_web2api.config import Config
from chatgpt_web2api.turn_anchor import TurnAnchor, TurnReconciliationError, TurnTextResult


def driver_for(chunks, snapshot, backend):
    driver = CDPDriver(cdp_port=9222)
    driver._read_assistant_count_baseline = AsyncMock(return_value=0)
    driver._capture_pre_send_fallback_anchor = AsyncMock(
        return_value=TurnAnchor("fixture", "fresh_chat")
    )
    driver.type_message = AsyncMock()
    driver.click_send = AsyncMock()
    driver._js_strict = AsyncMock(return_value="https://chatgpt.com/c/fixture-conversation")
    listener = MagicMock()
    listener.reenable_if_stale = AsyncMock()
    listener.wait_for_captured_uuid = AsyncMock(return_value="fixture-user")
    driver._identity_listener = listener
    scope = listener.arm_capture_scope.return_value
    driver._fetch_text_for_turn = AsyncMock(side_effect=AssertionError("No extra projection reads"))

    class Completion:
        last_dom_text = ""
        had_non_text_content = False
        anchors = []

        def take_completed_turn_text(self, conv, anchor):
            assert conv == "fixture-conversation"
            assert anchor.captured_user_message_id == "fixture-user"
            return TurnTextResult(status="matched", text=backend)

        async def stream_until_complete(self, **kwargs):
            self.anchors.append(kwargs["turn_anchor"])
            for text in chunks:
                yield StreamChunk(delta=text)
            self.last_dom_text = snapshot

    driver._completion = Completion()
    return driver, scope


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks,snapshot,backend",
    [
        ([], "", '{"ok":true}'),
        (['{"ok":true}'], '{"ok":true}', '{"ok":true}'),
        (['{"ok":'], '{"ok":', '{"ok":true}'),
        (['{"text":"', "中文😀"], '{"text":"中文😀', '{"text":"中文😀"}'),
        (["a", "b"], "ab", "abc"),
    ],
)
async def test_verified_prefix_exact_output(chunks, snapshot, backend):
    driver, scope = driver_for(chunks, snapshot, backend)
    output = [chunk async for chunk in driver.send_and_stream("fixture")]
    assert "".join(chunk.delta for chunk in output) == backend
    assert [chunk.finish_reason for chunk in output if chunk.finish_reason] == ["stop"]
    driver.type_message.assert_awaited_once()
    driver.click_send.assert_awaited_once()
    driver._fetch_text_for_turn.assert_not_awaited()
    scope.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks,snapshot,backend",
    [
        (['{"type":'], '{"type":', '{\n  "type": "batch", "operations": []\n}'),
        (["abc"], "abc", "xyz"),
        (["abcdef"], "abcdef", "abc"),
        (["prefix"], "prefix", "different-longer"),
        (["wrong"], "right", "right final"),
        (["right"], "wrong", "right final"),
        (["a", "b", "b"], "ab", "abc"),
    ],
)
async def test_mismatch_terminal_no_suffix_or_stop(chunks, snapshot, backend):
    driver, scope = driver_for(chunks, snapshot, backend)
    output = []
    with pytest.raises(TurnReconciliationError) as caught:
        async for chunk in driver.send_and_stream("fixture"):
            output.append(chunk)
    assert "".join(chunk.delta for chunk in output) == "".join(chunks)
    assert not any(chunk.finish_reason for chunk in output)
    assert caught.value.last_status == "text_mismatch"
    assert set(caught.value.diagnostic) == {
        "reason",
        "dom_text_length",
        "emitted_text_length",
        "backend_text_length",
    }
    assert backend not in str(caught.value)
    driver.type_message.assert_awaited_once()
    driver.click_send.assert_awaited_once()
    driver._fetch_text_for_turn.assert_not_awaited()
    scope.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [False, True])
async def test_nonstream_full_response_or_explicit_failure(mismatch):
    backend = '{"ok":true}'
    driver, _ = driver_for(
        ["bad"] if mismatch else ['{"ok":'], "bad" if mismatch else '{"ok":', backend
    )
    server = APIServer(Config.load(None), driver)
    if mismatch:
        with pytest.raises(TurnReconciliationError) as caught:
            await server._full_response(None, "auto", "fixture", 60)
        error = server._error_response(caught.value)
        assert error.status == 500 and "text_mismatch" in error.text
        assert server._last_successful_send_at is None
    else:
        response = await server._full_response(None, "auto", "fixture", 60)
        assert json.loads(response.body)["choices"][0]["message"]["content"] == backend
        assert server._last_successful_send_at is not None
    driver.type_message.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [False, True])
async def test_streaming_complete_or_error_never_false_stop(monkeypatch, mismatch):
    driver, _ = driver_for(
        ["bad"] if mismatch else ['{"ok":'], "bad" if mismatch else '{"ok":', '{"ok":true}'
    )
    server = APIServer(Config.load(None), driver)
    server._check_circuit_or_recover = AsyncMock()
    response = MagicMock()
    response.headers = {}
    response.prepare = AsyncMock()
    response.write = AsyncMock()
    response.write_eof = AsyncMock()
    monkeypatch.setattr("chatgpt_web2api.api_server.web.StreamResponse", lambda: response)
    await server._stream_response(MagicMock(), "auto", "fixture", 60)
    frames = [call.args[0].decode() for call in response.write.await_args_list]
    data = [json.loads(frame[len("data: ") :]) for frame in frames if frame.startswith("data: {")]
    choices = [event["choices"][0] for event in data]
    reasons = [choice["finish_reason"] for choice in choices if choice["finish_reason"]]
    if mismatch:
        assert reasons == ["error"] and server._last_successful_send_at is None
        assert any("text_mismatch" in choice["delta"].get("content", "") for choice in choices)
    else:
        assert reasons == ["stop"]
        assert "".join(choice["delta"].get("content", "") for choice in choices) == '{"ok":true}'
    assert frames[-1] == "data: [DONE]\n\n"
    driver.type_message.assert_awaited_once()
