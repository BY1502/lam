"""Real saved-session, idempotency and restart checks with zero generation intent."""
import json,sys,hashlib,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend import chat_flow
OUT=ROOT/'evaluation/results/chatbot-stream-20261102-01'
def api(path,body=None):
 req=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req) as f:return f.status,json.load(f)
def main():
 rows=[json.loads(l) for l in (OUT/'runs.jsonl').read_text().splitlines()];r=rows[0]['record'];sid=r['session_id'];rid=r['request_id'];before=json.dumps(chat_flow.get(sid,rid),ensure_ascii=False,sort_keys=True)
 status,replayed=api(f'/api/chat/sessions/{sid}/messages',{'question':r['question'],'request_id':rid});after=json.dumps(chat_flow.get(sid,rid),ensure_ascii=False,sort_keys=True)
 other=rows[-1]['record']['session_id']
 try:api(f'/api/chat/sessions/{other}/messages',{'question':r['question'],'request_id':rid});cross=None
 except urllib.error.HTTPError as e:cross=e.code
 interrupted=json.loads((OUT/'browser_interrupt_run.json').read_text())['record'];_,history=api('/api/chat/sessions/'+interrupted['session_id']);restored=next(x for x in history['turns'] if x['request_id']==interrupted['request_id'])
 with urllib.request.urlopen('http://127.0.0.1:8080/api/chat/sessions/'+sid+'/export') as f:export=[json.loads(l) for l in f]
 with urllib.request.urlopen(f'http://127.0.0.1:8080/api/chat/sessions/{sid}/messages/{rid}/events') as f:ev=[json.loads(l) for l in f]
 checks={'completed_idempotent_reused':replayed['reused'],'same_stored_record':before==after,'same_model_call_count':replayed['record']['llm_call_count']==r['llm_call_count'],'cross_session_id_conflict':cross==409,'post_restart_partial_exact':restored['reply']==interrupted['reply'],'post_restart_status_interrupted':restored['status']=='interrupted','export_no_gold_labels':all(not e['labels_are_gold'] and e['review_status']=='unreviewed' for e in export),'terminal_stream_is_one_snapshot':len(ev)==1 and ev[0]['type']=='snapshot'}
 assert all(checks.values()),checks
 (OUT/'read_only_checks.json').write_text(json.dumps({'checks':checks,'original_request_id':rid,'model_generation_calls_added':0,'test_method':'수락완료 요청ID 재제출은 DB 레코드를 재사용하고 종료 events는 snapshot만 읽음. 동일 원문 해시·저장 호출 수 유지.'},ensure_ascii=False,indent=2))
 print(json.dumps(checks,ensure_ascii=False))
if __name__=='__main__':main()
