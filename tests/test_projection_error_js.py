import json
import shutil
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatgpt_web2api.backend_projection import CONVERSATION_PROJECTION_JS


@pytest.mark.parametrize('status,header,body',[
    (429,'12',{'error':{'code':'history_rate_limit','type':'throttled','message':'DO_NOT_EXPORT','token':'DO_NOT_EXPORT'}}),
    (429,'Wed, 07 Oct 2026 00:00:30 GMT',{'error':{'code':'history_rate_limit'}}),
    (401,None,{'error':{'code':'auth_expired'}}),
])
def test_execute_projection_js_preserves_only_allowed_error_metadata(status,header,body):
    node=shutil.which('node')
    if not node: pytest.skip('Node required for offline JavaScript execution')
    fixture=json.dumps({'status':status,'header':header,'body':body})
    script='const f='+fixture+''';const __D={conv_id:'synthetic',token:'synthetic',limit:50,phase:'phase_1_completion_and_text',observation_id:'abc',trace:true};
let calls=0;const trace=[];console.debug=(...args)=>trace.push(args);
global.fetch=async()=>{calls++;return {ok:false,status:f.status,headers:{get:()=>f.header},json:async()=>f.body}};
'''+'Promise.resolve('+CONVERSATION_PROJECTION_JS+').then(raw=>console.log(JSON.stringify({payload:JSON.parse(raw),calls,trace})));'
    result=subprocess.run([node,'-e',script],check=True,capture_output=True,text=True,timeout=10)
    data=json.loads(result.stdout)
    assert data['calls']==1
    assert data['payload']['__status']==status
    assert data['payload']['__retry_after']==header
    assert data['payload']['__error_metadata']['code']==body['error']['code']
    assert 'DO_NOT_EXPORT' not in result.stdout
    events=[json.loads(item[1]) for item in data['trace']]
    assert [item['event'] for item in events]==['start','end']
    assert events[-1]['status']==status


@pytest.mark.asyncio
async def test_trace_template_executes_inside_real_transport_wrapper(monkeypatch):
    from chatgpt_web2api.backend_client import BackendClient
    from chatgpt_web2api.projection_diagnostics import projection_phase
    node=shutil.which('node')
    if not node: pytest.skip('Node required for offline JavaScript execution')
    monkeypatch.setenv('W2A_PROJECTION_TRACE','1')
    driver=MagicMock()
    driver.ensure_token=AsyncMock()
    driver._access_token='synthetic-token'
    driver._js_with_data_strict=AsyncMock(return_value='{"nodes":{},"current_node":null}')
    with projection_phase('phase_1_completion_and_text'):
        await BackendClient(driver)._fetch_recent_conversation_projection('synthetic')
    template,data=driver._js_with_data_strict.call_args.args
    assert 'sourceURL=w2a-projection/phase_1_completion_and_text/' in template
    wrapped=f'( (__D) => ({template}) )({json.dumps(data)})'
    script='''const trace=[];console.debug=(...args)=>trace.push(args);
global.fetch=async()=>({ok:true,status:200,json:async()=>({mapping:{},current_node:null})});
'''+f'Promise.resolve({wrapped}).then(raw=>console.log(JSON.stringify({{payload:JSON.parse(raw),trace}})));'
    result=subprocess.run([node,'-e',script],check=True,capture_output=True,text=True,timeout=10)
    output=json.loads(result.stdout)
    assert output['payload']=={'nodes':{},'current_node':None}
    events=[json.loads(item[1]) for item in output['trace']]
    assert [item['event'] for item in events]==['start','end']
    assert all(item['phase']=='phase_1_completion_and_text' for item in events)
    assert 'synthetic-token' not in result.stdout
