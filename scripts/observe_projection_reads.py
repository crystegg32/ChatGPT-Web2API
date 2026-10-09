"""Passive, bounded CDP observation. Never fetches, navigates or sends a turn.

For exact Bridge phases enable W2A_PROJECTION_TRACE=1 in a separately authorized
runtime. Without phase markers, provenance is recorded as unknown, not inferred.
Only localhost target discovery and existing CDP response buffers are read.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

import websockets
from chatgpt_web2api.projection_diagnostics import safe_error_metadata

PHASES = {'unspecified','pre_send_baseline','phase_1_completion_and_text','turn_completion_and_text',
          'turn_completion','turn_text','driver_final_text','send_trace','send_confirmation_reconciliation',
          'phase_2_completion','pre_stall_reconciliation'}


def classify(status):
    return {200:'success',401:'auth_required',404:'not_found',429:'rate_limited'}.get(status,'http_error')


class Capture:
    def __init__(self, conversation):
        self.endpoint='/backend-api/conversation/'+conversation
        self.records=[]
        self.by_request={}
        self.markers={}
        self.body_queries={}
        self.next_id=100

    def accept(self,event):
        """Return only buffer-read commands; no HTTP or Runtime.evaluate calls."""
        p=event.get('params',{})
        method=event.get('method')
        if method=='Runtime.consoleAPICalled':
            args=p.get('args',[])
            if len(args)!=2 or args[0].get('value')!='W2A_PROJECTION_READ': return None
            try: marker=json.loads(args[1].get('value',''))
            except (ValueError,TypeError): return None
            uid=marker.get('id')
            if (not isinstance(uid,str) or len(uid)!=32 or any(c not in '0123456789abcdef' for c in uid)
                    or marker.get('phase') not in PHASES): return None
            if marker.get('event')=='start': self.markers[uid]={'phase':marker['phase'],'record':None,'start_ms':marker.get('time_ms')}
            elif marker.get('event')=='end':
                tracked=self.markers.pop(uid,None)
                if tracked and tracked['record']:
                    tracked['record']['bridge_end_ms']=marker.get('time_ms')
                    tracked['record']['bridge_classification']=classify(marker['status']) if marker.get('status') is not None else 'transport_error'
        elif method=='Network.requestWillBeSent':
            request=p.get('request',{})
            if request.get('method')!='GET' or urlsplit(request.get('url','')).path!=self.endpoint: return None
            record={'phase':'unknown','source':'unattributed','start_monotonic':p.get('timestamp'),
                    'start_epoch_seconds':p.get('wallTime'),'end_monotonic':None,'classification':'pending'}
            wall_ms=p.get('wallTime',0)*1000
            frames=p.get('initiator',{}).get('stack',{}).get('callFrames',[])
            ids=set()
            for frame in frames:
                match=re.fullmatch(r'w2a-projection/([a-z0-9_]+)/([a-f0-9]{32})',frame.get('url',''))
                if match and match[1] in PHASES:
                    ids.add((match[2],match[1]))
            candidates=[item for uid,item in self.markers.items() if (uid,item['phase']) in ids
                        and item['record'] is None and isinstance(item['start_ms'],(int,float))
                        and 0<=wall_ms-item['start_ms']<=250]
            if len(candidates)==1:
                record.update({'phase':candidates[0]['phase'],'source':'bridge_trace'})
                record['bridge_start_ms']=candidates[0]['start_ms']
                candidates[0]['record']=record
            self.by_request[p['requestId']]=record
            self.records.append(record)
        elif method=='Network.responseReceived' and p.get('requestId') in self.by_request:
            record=self.by_request[p['requestId']]
            response=p.get('response',{})
            record['status']=response.get('status')
            record['classification']=classify(record['status'])
            headers={k.lower():v for k,v in response.get('headers',{}).items()}
            # Preserve only valid Retry-After provenance, not arbitrary headers.
            from chatgpt_web2api.projection_diagnostics import parse_upstream_retry_after
            retry=parse_upstream_retry_after(headers.get('retry-after'))
            record['retry_after']=retry if record['status']==429 else None
        elif method in {'Network.loadingFinished','Network.loadingFailed'} and p.get('requestId') in self.by_request:
            record=self.by_request[p['requestId']]
            record['end_monotonic']=p.get('timestamp')
            if isinstance(record['start_monotonic'],(int,float)) and isinstance(record['end_monotonic'],(int,float)):
                record['duration_seconds']=record['end_monotonic']-record['start_monotonic']
                record['end_epoch_seconds']=record['start_epoch_seconds']+record['duration_seconds']
            if method=='Network.loadingFailed': record['classification']='transport_error'
            elif record.get('status')==429:
                query=self.next_id; self.next_id+=1
                self.body_queries[query]=record
                return {'id':query,'method':'Network.getResponseBody','params':{'requestId':p['requestId']}}
        elif event.get('id') in self.body_queries:
            record=self.body_queries.pop(event['id'])
            body=event.get('result',{})
            try:
                value=json.loads(body['body']) if not body.get('base64Encoded') else None
            except (ValueError,TypeError,KeyError): value=None
            error=value.get('error',value.get('detail',value)) if isinstance(value,dict) else None
            record['upstream_error']=safe_error_metadata(error)
            record['error_body_classification']='structured_error' if isinstance(error,dict) else 'unavailable_or_unstructured'
        return None


async def observe(args):
    # Local CDP inventory only; never calls ChatGPT endpoints.
    with urlopen(f'http://127.0.0.1:{args.cdp_port}/json/list',timeout=5) as response:
        target=next(t for t in json.load(response) if t['id']==args.target_id)
    capture=Capture(args.conversation_id)
    started=time.monotonic()
    async with websockets.connect(target['webSocketDebuggerUrl'],max_size=8*1024*1024) as ws:
        for uid,method in [(1,'Network.enable'),(2,'Runtime.enable')]:
            await ws.send(json.dumps({'id':uid,'method':method,'params':{}}))
        while time.monotonic()-started<args.seconds:
            try: event=json.loads(await asyncio.wait_for(ws.recv(),timeout=min(.5,max(.001,args.seconds-(time.monotonic()-started)))))
            except asyncio.TimeoutError: continue
            command=capture.accept(event)
            if command: await ws.send(json.dumps(command))
    report={'reads':capture.records,'observed_get_count':len(capture.records),
            'chatgpt_requests_generated_by_recorder':0,'new_turns_generated_by_recorder':0,
            'coverage':'Only this observation window; unknown phases are not attributed to UI or Bridge.'}
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'observed_get_count':len(capture.records),'generated_requests':0}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cdp-port',type=int,default=9222)
    parser.add_argument('--target-id',required=True)
    parser.add_argument('--conversation-id',required=True)
    parser.add_argument('--seconds',type=float,default=30)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.seconds<=300: parser.error('seconds must be within 1..300')
    asyncio.run(observe(args))
