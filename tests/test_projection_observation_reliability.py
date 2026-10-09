import json
from datetime import datetime, timezone
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatgpt_web2api.api_server import APIServer
from chatgpt_web2api.backend_client import BackendClient
from chatgpt_web2api.cdp_driver import AuthExpiredError, ObservationRateLimitError
from chatgpt_web2api.completion_detector import CompletionDetector
from chatgpt_web2api.projection_diagnostics import parse_upstream_retry_after, safe_error_metadata
from chatgpt_web2api.turn_anchor import TurnAnchor


@pytest.mark.parametrize('value,seconds,source,reason', [
    ('15',15,'upstream_seconds',None), ('0',0,'upstream_seconds',None),
    ('Wed, 07 Oct 2026 00:00:30 GMT',30,'upstream_http_date',None),
    ('Tue, 06 Oct 2026 23:59:00 GMT',0,'upstream_http_date',None),
    (None,60,'bridge_fallback','missing_upstream_header'),
    ('',60,'bridge_fallback','invalid_upstream_header'),
    ('nonsense',60,'bridge_fallback','invalid_upstream_header'),
    ('-1',60,'bridge_fallback','invalid_upstream_header'),
    ('1.5',60,'bridge_fallback','invalid_upstream_header'),
])
def test_retry_after_provenance(value,seconds,source,reason):
    result=parse_upstream_retry_after(value,now=datetime(2026,10,7,tzinfo=timezone.utc))
    assert (result['seconds'],result['source'],result['reason'])==(seconds,source,reason)
    if source=='bridge_fallback': assert result['upstream_header'] is None


def test_error_metadata_excludes_arbitrary_content_and_secrets():
    assert safe_error_metadata({'code':'history_rate_limit','type':'throttled','message':'private',
                                'token':'private','cookies':'private'}) == {'code':'history_rate_limit','type':'throttled'}
    for value in ['someone@example.com','C:/Users/private','eyJ'+'a'*30,'sk-private-secret']:
        assert safe_error_metadata({'code':value})=={'code':'<redacted>'}


def client_with(mapping):
    driver=MagicMock()
    driver.ensure_token=AsyncMock()
    driver._access_token='synthetic-token'
    driver._breakers=None
    driver._js_with_data_strict=AsyncMock(return_value=json.dumps(mapping))
    return BackendClient(driver),driver


def mapping():
    return {'current_node':'a','nodes':{
        'u':{'id':'u','role':'user','parent':None,'children':['r'],'create_time':10,
             'content_type':'text','text':'prompt','end_turn':False},
        'r':{'id':'r','role':'assistant','parent':'u','children':['a'],'create_time':11,
             'content_type':'reasoning_recap','text':'','end_turn':False},
        'a':{'id':'a','role':'assistant','parent':'r','children':[],'create_time':12,
             'content_type':'text','text':'reply','end_turn':True}}}


def anchor():
    return TurnAnchor('prompt','captured_id',captured_user_message_id='u',conversation_id_at_capture='conv')


@pytest.mark.asyncio
@pytest.mark.parametrize('order', [('a', 'new'), ('new', 'a')])
@pytest.mark.parametrize('new_time', [13, 12])
async def test_completed_text_selectors_agree_before_exact_phase_one_handoff(order, new_time):
    data = mapping()
    data['nodes']['r']['children'] = list(order)
    data['nodes']['new'] = {
        **data['nodes']['a'], 'id': 'new', 'create_time': new_time, 'text': 'new reply',
    }
    # Equal timestamps retain the existing text selector's traversal-order tie rule.
    expected_id = 'new' if new_time > 12 else order[0]
    expected_text = data['nodes'][expected_id]['text']
    client, driver = client_with(data)
    end, text = await client._fetch_turn_results('conv', anchor())
    assert end.status == text.status == 'matched'
    assert end.diagnostic['assistant_node'] == text.diagnostic['assistant_node'] == expected_id
    assert end.diagnostic['user_node'] == text.diagnostic['user_node'] == 'u'
    driver._js_with_data_strict.reset_mock()
    driver._get_live_conversation_id_best_effort = AsyncMock(return_value='conv')
    driver._fetch_turn_results = client._fetch_turn_results
    detector = CompletionDetector(driver)
    assert await detector._phase_one_backend_text(driver, anchor(), []) == expected_text
    driver._js_with_data_strict.assert_awaited_once()
    cached = detector.take_completed_turn_text('conv', anchor())
    assert cached.diagnostic['assistant_node'] == expected_id
    assert cached.text == expected_text
    assert detector.take_completed_turn_text('conv', anchor()) is None


@pytest.mark.asyncio
async def test_same_fresh_projection_drives_both_selectors_and_one_use_handoff():
    client,driver=client_with(mapping())
    driver._get_live_conversation_id_best_effort=AsyncMock(return_value='conv')
    driver._fetch_turn_results=client._fetch_turn_results
    detector=CompletionDetector(driver)
    assert await detector._phase_one_backend_text(driver,anchor(),[])=='reply'
    driver._js_with_data_strict.assert_awaited_once()
    result=detector.take_completed_turn_text('conv',anchor())
    assert result.status=='matched' and result.text=='reply'
    assert result.diagnostic['user_node']=='u' and result.diagnostic['assistant_node']=='a'
    assert detector.take_completed_turn_text('conv',anchor()) is None
    driver._js_with_data_strict.assert_awaited_once()  # Handoff performs no fetch.


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['old_turn','wrong_identity','wrong_assistant_identity','wrong_parent','unfinished','empty','blank'])
async def test_projection_never_completes_an_unproven_turn(case):
    data=mapping()
    turn=anchor()
    if case=='old_turn': turn=replace(turn,captured_user_message_id='new-user')
    if case=='wrong_identity': data['nodes']['u']['role']='assistant'
    if case=='wrong_assistant_identity': data['nodes']['a']['id']='different-assistant'
    if case=='wrong_parent': data['nodes']['a']['parent']='unrelated'
    if case=='unfinished': data['nodes']['a']['end_turn']=False
    if case=='empty': data['nodes']['a']['text']=''
    if case=='blank': data['nodes']['a']['text']='  '
    client,driver=client_with(data)
    driver._get_live_conversation_id_best_effort=AsyncMock(return_value='conv')
    driver._fetch_turn_results=client._fetch_turn_results
    detector=CompletionDetector(driver)
    assert await detector._phase_one_backend_text(driver,turn,[]) is None
    assert detector.take_completed_turn_text('conv',turn) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('status,error',[(401,AuthExpiredError),(429,ObservationRateLimitError)])
async def test_shared_projection_auth_and_rate_limit_propagate_without_success(status,error):
    client,driver=client_with({'__status':status})
    driver._get_live_conversation_id_best_effort=AsyncMock(return_value='conv')
    driver._fetch_turn_results=client._fetch_turn_results
    detector=CompletionDetector(driver)
    with pytest.raises(error): await detector._phase_one_backend_text(driver,anchor(),[])
    assert detector.take_completed_turn_text('conv',anchor()) is None
    driver._js_with_data_strict.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('conv,turn',[('other',anchor()),('conv',replace(anchor(),captured_user_message_id='other'))])
async def test_completed_handoff_rejects_other_conversation_or_turn(conv,turn):
    client,driver=client_with(mapping())
    driver._get_live_conversation_id_best_effort=AsyncMock(return_value='conv')
    driver._fetch_turn_results=client._fetch_turn_results
    detector=CompletionDetector(driver)
    await detector._phase_one_backend_text(driver,anchor(),[])
    assert detector.take_completed_turn_text(conv,turn) is None
    assert detector.take_completed_turn_text('conv',anchor()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('header,source', [('17','upstream_seconds'),(None,'bridge_fallback'),('invalid','bridge_fallback')])
async def test_rest_keeps_upstream_provenance_and_redacted_error_code(header,source):
    client,driver=client_with({'__status':429,'__retry_after':header,
                              '__error_metadata':{'code':'history_rate_limit','message':'private'}})
    with pytest.raises(ObservationRateLimitError) as caught:
        await client._fetch_recent_conversation_projection('conv')
    response=APIServer.__new__(APIServer)._error_response(caught.value)
    assert response.status==429
    assert response.headers['Retry-After']==('17' if header=='17' else '60')
    assert response.headers['X-Bridge-Retry-After-Source']==source
    error=json.loads(response.text)['error']
    assert error['retry_after_source']==source
    assert error['upstream_error']=={'code':'history_rate_limit'}
    assert 'private' not in response.text


@pytest.mark.asyncio
async def test_rest_http_date_provenance():
    from datetime import timedelta
    from email.utils import format_datetime
    header=format_datetime(datetime.now(timezone.utc)+timedelta(seconds=60),usegmt=True)
    client,_=client_with({'__status':429,'__retry_after':header})
    with pytest.raises(ObservationRateLimitError) as caught:
        await client._fetch_recent_conversation_projection('conv')
    response=APIServer.__new__(APIServer)._error_response(caught.value)
    assert 58<=int(response.headers['Retry-After'])<=60
    assert response.headers['X-Bridge-Retry-After-Source']=='upstream_http_date'
    assert json.loads(response.text)['error']['upstream_retry_after']==header


@pytest.mark.asyncio
async def test_real_driver_phase_one_and_tail_fetch_projection_only_once(monkeypatch):
    from chatgpt_web2api.cdp_driver import CDPDriver
    clock=[0.0]
    monkeypatch.setattr('chatgpt_web2api.completion_detector.time.monotonic',lambda:clock[0])
    async def sleep(seconds): clock[0]+=seconds
    monkeypatch.setattr('chatgpt_web2api.completion_detector.asyncio.sleep',sleep)
    driver=CDPDriver(cdp_port=9222)
    driver._read_assistant_count_baseline=AsyncMock(return_value=0)
    driver._capture_pre_send_fallback_anchor=AsyncMock(return_value=anchor())
    driver._verify_send_acknowledged=AsyncMock(return_value=True)
    driver.type_message=AsyncMock()
    driver.click_send=AsyncMock()
    driver.ensure_token=AsyncMock()
    driver._access_token='synthetic-token'
    driver._js_with_data_strict=AsyncMock(return_value=json.dumps(mapping()))
    driver._fetch_text_for_turn=AsyncMock(side_effect=AssertionError('Unexpected duplicate tail fetch'))
    driver._get_live_conversation_id_best_effort=AsyncMock(return_value='conv')
    async def js(expression,**kwargs):
        if 'body.innerText' in expression: return json.dumps({'text':'normal'})
        if 'location.href' in expression: return 'https://chatgpt.com/c/conv'
        return '0'
    driver._js_strict=AsyncMock(side_effect=js)
    chunks=[chunk async for chunk in driver.send_and_stream('prompt',timeout=100)]
    assert ''.join(chunk.delta for chunk in chunks if chunk.delta)=='reply'
    assert chunks[-1].finish_reason=='stop'
    driver._js_with_data_strict.assert_awaited_once()
    driver._fetch_text_for_turn.assert_not_awaited()
    driver.click_send.assert_awaited_once()
