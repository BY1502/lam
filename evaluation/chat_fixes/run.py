"""Sequential actual chatbot acceptance/stream verification. Saved IDs are never regenerated."""
import hashlib,json,os,sys,time,uuid,urllib.request,subprocess
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend import chat_flow
OUT=ROOT/'evaluation/results/chat-fixes-20261102-01'
def api(path,body=None):
 request=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(request,timeout=240) as f:return json.load(f)
def readlines(name):return [json.loads(l) for l in (OUT/name).read_text().splitlines()] if (OUT/name).exists() else []
def append(name,row):
 with (OUT/name).open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
def main():
 files=[p for d in ['backend','frontend','prompts','settings','taxonomy'] for p in (ROOT/d).rglob('*') if p.is_file() and '__pycache__' not in p.parts]+[OUT/'questions_gold.json']
 hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
 manifest=OUT/'manifest.json'
 if manifest.exists():assert json.loads(manifest.read_text())['hashes']==hashes
 else:
  container=json.loads(subprocess.check_output(['docker','inspect','vllm-qwen'],text=True))[0]
  with urllib.request.urlopen('http://127.0.0.1:8001/version') as f:engine=json.load(f)
  manifest.write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'hashes':hashes,'versions':chat_flow.versions(),'engine':engine,'image':container['Config']['Image'],'image_id':container['Image'],'model_command':container['Config']['Cmd'],'policy':'기존 vLLM·설정·출력 한도 유지. 자체 순차 실행. 다른 사용자 작업 종료하지 않음.','measurement':'캐시 초기화/재적재/별도 워밍업 없음. 공유 서버 부하 통제 안 됨.'},ensure_ascii=False,indent=2))
 gold=json.loads((OUT/'questions_gold.json').read_text());sessions=json.loads((OUT/'sessions.json').read_text()) if (OUT/'sessions.json').exists() else {}
 done={(r['conversation'],r['turn']) for r in readlines('runs.jsonl')};dispatch={(r['conversation'],r['turn']):r for r in readlines('dispatches.jsonl')}
 for conversation in gold['conversations']:
  cid=conversation['id']
  if cid not in sessions:sessions[cid]=api('/api/chat/sessions',{})['session_id'];(OUT/'sessions.json').write_text(json.dumps(sessions))
  sid=sessions[cid]
  for turn,case in enumerate(conversation['turns'],1):
   if (cid,turn) in done:continue
   assert hashes=={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
   start=time.perf_counter();events=[];key=(cid,turn)
   if key in dispatch:rid=dispatch[key]['request_id'];accepted={'record':api(f'/api/chat/sessions/{sid}/messages/{rid}'),'recovered_from_store':True}
   else:
    rid=uuid.uuid4().hex;append('dispatches.jsonl',{'conversation':cid,'turn':turn,'request_id':rid,'session_id':sid,'question':case['question']});accepted=api(f'/api/chat/sessions/{sid}/messages',{'question':case['question'],'request_id':rid})
   accept_seconds=time.perf_counter()-start
   try:
    with urllib.request.urlopen(f'http://127.0.0.1:8080/api/chat/sessions/{sid}/messages/{rid}/events',timeout=240) as f:
     for line in f:events.append({'received_seconds':time.perf_counter()-start,**json.loads(line)})
   except Exception as e:append('client_errors.jsonl',{'request_id':rid,'error':repr(e)})
   r=chat_flow.get(sid,rid)
   if r['status']=='pending':raise RuntimeError('보존된 요청이 아직 pending. 재생성하지 않고 종료.')
   actual=[q['intent_id'] for q in (r.get('analysis') or {}).get('requests',[])];validated=False;ordered=True
   for event in events:
    if event['type']=='analysis' or event.get('record',{}).get('analysis_validated'):validated=True
    if event['type']=='delta' and not validated:ordered=False
   append('runs.jsonl',{'conversation':cid,'turn':turn,'question':case['question'],'expected_ids':case['expected'],'actual_ids':actual,'exact_ids':set(actual)==set(case['expected']),'criterion':case['criterion'],'accepted':accepted,'accept_http_seconds':accept_seconds,'client_total_seconds':time.perf_counter()-start,'validated_before_body':ordered,'events':events,'record':r})
   print(cid,turn,r['status'],actual,round(r['timings']['total_seconds'],3),flush=True)
 (OUT/'completion.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'hashes_unchanged':True,'rows':len(readlines('runs.jsonl'))}))
if __name__=='__main__':main()
