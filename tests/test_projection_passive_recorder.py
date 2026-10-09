import importlib.util
import json
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('projection_recorder',Path(__file__).parents[1]/'scripts/observe_projection_reads.py')
recorder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)


def event(method,**params): return {'method':method,'params':params}


def request(uid='request',timestamp=10,method='GET',tagged=False):
    return event('Network.requestWillBeSent',requestId=uid,timestamp=timestamp,wallTime=10,
                 initiator={'stack':{'callFrames':[{'url':'w2a-projection/phase_1_completion_and_text/'+'a'*32}] if tagged else []}},
                 request={'url':'https://chatgpt.com/backend-api/conversation/conv?offset=0&limit=50',
                          'method':method,'headers':{'Authorization':'DO_NOT_EXPORT','Cookie':'DO_NOT_EXPORT'}})


def marker(kind):
    return event('Runtime.consoleAPICalled',args=[{'value':'W2A_PROJECTION_READ'},
        {'value':json.dumps({'id':'a'*32,'phase':'phase_1_completion_and_text','event':kind,'time_ms':10000,'status':429 if kind=='end' else None})}])


def test_passive_capture_source_times_classification_and_privacy():
    capture=recorder.Capture('conv')
    assert capture.accept(marker('start')) is None
    assert capture.accept(request(tagged=True)) is None
    assert capture.accept(event('Network.responseReceived',requestId='request',response={
        'status':429,'headers':{'Retry-After':'20','Set-Cookie':'DO_NOT_EXPORT','Authorization':'DO_NOT_EXPORT'}})) is None
    command=capture.accept(event('Network.loadingFinished',requestId='request',timestamp=11))
    assert command['method']=='Network.getResponseBody'  # Read existing buffer, never fetch.
    capture.accept({'id':command['id'],'result':{'body':json.dumps({'error':{
        'code':'history_rate_limit','message':'DO_NOT_EXPORT','token':'DO_NOT_EXPORT'},
        'mapping':{'private':'DO_NOT_EXPORT'}})}})
    capture.accept(marker('end'))
    record=capture.records[0]
    assert record['phase']=='phase_1_completion_and_text'
    assert record['start_monotonic']==10 and record['end_monotonic']==11
    assert record['classification']=='rate_limited'
    assert record['retry_after']['source']=='upstream_seconds'
    assert record['upstream_error']=={'code':'history_rate_limit'}
    output=json.dumps(record)
    for denied in ['DO_NOT_EXPORT','Authorization','Cookie','mapping','https://','conversation/conv']:
        assert denied not in output


@pytest.mark.parametrize('status,classification',[(200,'success'),(401,'auth_required'),(404,'not_found'),(500,'http_error')])
def test_success_and_other_errors_never_fetch_or_read_conversation_body(status,classification):
    capture=recorder.Capture('conv')
    capture.accept(request())
    assert capture.accept(event('Network.responseReceived',requestId='request',response={'status':status,'headers':{}})) is None
    assert capture.accept(event('Network.loadingFinished',requestId='request',timestamp=11)) is None
    assert capture.records[0]['classification']==classification
    assert capture.records[0]['phase']=='unknown'


def test_post_and_nonmatching_conversations_not_recorded():
    capture=recorder.Capture('another-conversation')
    assert capture.accept(request()) is None
    capture=recorder.Capture('conv')
    assert capture.accept(request(method='POST')) is None
    assert not capture.records


def test_unresolved_network_read_has_explicit_unknown_end():
    capture=recorder.Capture('conv')
    capture.accept(request())
    record=capture.records[0]
    assert record['end_monotonic'] is None and record['classification']=='pending'


def test_replayed_old_console_marker_cannot_attribute_a_new_read():
    capture=recorder.Capture('conv')
    old=marker('start')
    value=json.loads(old['params']['args'][1]['value'])
    value['time_ms']=1000
    old['params']['args'][1]['value']=json.dumps(value)
    capture.accept(old)
    capture.accept(request(tagged=True))
    assert capture.records[0]['source']=='unattributed'
    assert capture.records[0]['phase']=='unknown'
