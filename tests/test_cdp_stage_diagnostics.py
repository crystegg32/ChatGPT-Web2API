import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from chatgpt_web2api.cdp_diagnostics import utf8_size
from chatgpt_web2api.cdp_driver import CDPDriver
from scripts.evaluate_reviewer_batch_caps import group_read_operations
from tests.test_final_text_consistency import driver_for


class Socket:
    def __init__(self):
        self.sent = []
        self.frames = asyncio.Queue()
        self.sending = asyncio.Event()

    async def send(self, data):
        self.sent.append(json.loads(data))
        self.sending.set()

    async def recv(self):
        return json.dumps(await self.frames.get())


def setup():
    driver = CDPDriver(cdp_port=9222)
    driver._ws = Socket()
    driver.reconnect = AsyncMock()
    driver._reader_task = asyncio.create_task(driver._transport._reader_loop())
    return driver


async def close(driver):
    driver._reader_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await driver._reader_task


def records(caplog):
    return [json.loads(r.message.split('CDP_STAGE ', 1)[1])
            for r in caplog.records if 'CDP_STAGE ' in r.message]


@pytest.mark.asyncio
async def test_out_of_order_exact_ids_and_private_metadata(caplog):
    caplog.set_level(logging.DEBUG)
    driver = setup()
    private = 'PRIVATE_SOURCE_COOKIE_AUTH_中文😀'
    first = asyncio.create_task(driver._cdp('Input.insertText', {'text': private}))
    await driver._ws.sending.wait()
    second = asyncio.create_task(driver._cdp('Runtime.evaluate', {'expression': private}))
    await asyncio.sleep(0)
    await driver._ws.frames.put({'id': 2, 'result': {'private': private}})
    await driver._ws.frames.put({'id': 1, 'result': {'ok': True}})
    assert (await second)['id'] == 2 and (await first)['id'] == 1
    assert not driver._pending
    assert not driver._transport._observations
    events = records(caplog)
    for phase in ('start', 'sent', 'response'):
        event = next(r for r in events if r['id'] == 1 and r['phase'] == phase)
        assert event['text_characters'] == len(private)
        assert event['text_utf8_bytes'] == len(private.encode('utf-8'))
        assert event['timeout_seconds'] == 15
        assert event['elapsed_ms'] >= 0
    assert private not in caplog.text and 'PRIVATE_SOURCE' not in caplog.text
    await close(driver)


@pytest.mark.asyncio
async def test_timeout_late_response_does_not_resolve_next_command(caplog):
    caplog.set_level(logging.DEBUG)
    driver = setup()
    with pytest.raises(TimeoutError, match='CDP timeout: Input.insertText'):
        await driver._cdp('Input.insertText', {'text': 'secret'}, timeout=0.01)
    assert not driver._pending
    following = asyncio.create_task(driver._cdp('Runtime.evaluate', {}))
    await asyncio.sleep(0)
    await driver._ws.frames.put({'id': 1, 'result': {'secret': 'never-log'}})
    await driver._ws.frames.put({'id': 2, 'result': {'ok': True}})
    assert (await following)['id'] == 2
    assert [r['phase'] for r in records(caplog) if r['id'] == 1] == [
        'start', 'sent', 'timeout', 'late_response']
    driver.reconnect.assert_not_awaited()
    assert len(driver._ws.sent) == 2
    assert 'never-log' not in caplog.text
    await close(driver)


@pytest.mark.asyncio
@pytest.mark.parametrize('during_send', [False, True])
async def test_caller_cancellation_cleans_pending_and_never_resends(during_send):
    driver = setup()
    if during_send:
        async def blocked_send(data):
            driver._ws.sending.set()
            await asyncio.Future()
        driver._ws.send = blocked_send
    call = asyncio.create_task(driver._cdp('Input.insertText', {'text': 'private'}))
    await driver._ws.sending.wait()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert not driver._pending
    driver.reconnect.assert_not_awaited()
    await close(driver)


@pytest.mark.asyncio
async def test_timeout_metadata_bounded_without_retaining_text():
    driver = setup()
    for _ in range(130):
        with pytest.raises(TimeoutError):
            await driver._cdp('Input.insertText', {'text': 'PRIVATE'}, timeout=0.0001)
    assert len(driver._transport._observations) == 128
    assert not driver._pending
    assert 'PRIVATE' not in repr(driver._transport._observations)
    await close(driver)


@pytest.mark.asyncio
async def test_send_failure_safe_phase_and_cleanup(caplog):
    caplog.set_level(logging.INFO)
    driver = setup()
    driver._ws.send = AsyncMock(side_effect=ValueError('PRIVATE_RAW_EXCEPTION'))
    with pytest.raises(ValueError):
        await driver._cdp('Input.insertText', {'text': 'PRIVATE_TEXT'})
    assert not driver._pending and not driver._transport._observations
    assert records(caplog)[-1]['phase'] == 'send_error'
    assert 'PRIVATE' not in caplog.text
    driver.reconnect.assert_not_awaited()
    await close(driver)


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['界😀' * 10000, '\ud800'], ids=['large_unicode', 'invalid_unicode'])
async def test_size_observation_never_changes_wire_text_or_raises(caplog, text):
    caplog.set_level(logging.INFO)
    driver = setup()
    call = asyncio.create_task(driver._cdp('Input.insertText', {'text': text}))
    await driver._ws.sending.wait()
    assert driver._ws.sent[0]['params']['text'] == text
    await driver._ws.frames.put({'id': 1, 'result': {}})
    assert (await call)['id'] == 1
    assert not driver._pending
    assert records(caplog)[0]['text_utf8_bytes'] == utf8_size(text)
    assert utf8_size('\ud800') is None
    await close(driver)


def test_cap_study_keeps_continuation_call_steps_separate():
    rows = [{'call': 1, 'step': 1, 'type': 'read', 'status': 'ok'},
            {'call': 2, 'step': 1, 'type': 'read', 'status': 'ok'},
            {'call': 2, 'step': 1, 'type': 'search', 'status': 'ok'}]
    grouped = group_read_operations(rows)
    assert set(grouped) == {(1, 1), (2, 1)}
    assert all(len(items) == 1 for items in grouped.values())


@pytest.mark.asyncio
async def test_real_typing_timeout_no_verification_retry_click_or_completion():
    driver, scope = driver_for([], '', '')
    driver._js = AsyncMock(return_value='composer')
    driver._detect_select_all_modifier = AsyncMock(return_value=2)
    driver._verify_composer_text = AsyncMock()

    async def cdp(method, params):
        if method == 'Input.insertText':
            raise TimeoutError('CDP timeout: Input.insertText')
        return {}
    driver._cdp = AsyncMock(side_effect=cdp)
    driver.type_message = driver._dom.type_message
    with pytest.raises(TimeoutError, match='Input.insertText'):
        async for _ in driver.send_and_stream('PRIVATE_SOURCE'):
            pytest.fail('No completion expected')
    driver._verify_composer_text.assert_not_awaited()
    driver.click_send.assert_not_awaited()
    assert [call.args[0] for call in driver._cdp.await_args_list].count('Input.insertText') == 1
    assert not driver._completion.anchors
    scope.close.assert_called_once()
