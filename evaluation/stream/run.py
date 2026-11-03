"""Sequential actual chat HTTP acceptance+NDJSON stream; fixed gold never enters model messages."""
import json,uuid,urllib.request,time,hashlib,sys,os,subprocess
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend import chat_flow,chatbot
OUT=ROOT/'evaluation/results/chatbot-stream-20261102-01'
def api(path,body=None):
 req=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=240) as f:return json.load(f)
def append(name,value):
 with (OUT/name).open('a') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
def read(name):return [json.loads(l) for l in (OUT/name).read_text().splitlines()] if (OUT/name).exists() else []
def main():
 gold=json.loads((OUT/'questions_gold.json').read_text());done={(r['conversation'],r['turn']) for r in read('runs.jsonl')};dispatch={(r['conversation'],r['turn']):r for r in read('dispatches.jsonl')};sessions=json.loads((OUT/'sessions.json').read_text()) if (OUT/'sessions.json').exists() else {}
 files=[p for d in ['backend','frontend','settings','prompts'] for p in (ROOT/d).rglob('*') if p.is_file() and p.suffix in ['.py','.md','.html','.css','.js','.json']];files += [ROOT/'taxonomy/intents_v2_ko.json',OUT/'questions_gold.json']
 hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files};mp=OUT/'manifest.json'
 if mp.exists():assert json.loads(mp.read_text())['hashes']==hashes,'고정 입력 변경; 새 실행 ID와 원본 구분 필요'
 else:
  with urllib.request.urlopen('http://127.0.0.1:8001/version') as f:version=json.load(f)
  container=json.loads(subprocess.check_output(['docker','inspect','vllm-qwen'],text=True))[0]
  mp.write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat(),'versions':chat_flow.versions(),'hashes':hashes,'model':{'version':version,'image':container['Config']['Image'],'image_id':container['Image'],'cmd':container['Config']['Cmd'],'device_requests':container['HostConfig']['DeviceRequests']},'inference':{'analysis':4096,'response':1536,'think':False,'temperature':0,'seed':20261012},'policy':'자체 요청은 순차, 다른 API 이용 작업 중지 안 함. 자동 재시도·답변/정답 보정 없음. 모델 서버 변경/캐시 초기화 안 함. 실제 원문 기록을 해당 웹 경로에서 읽음. 준비 요청을 따로 추가하지 않아 최초 요청을 cold/warm 판별 불명으로 표시.'},ensure_ascii=False,indent=2))
 for conv in gold['conversations']:
  cid=conv['id']
  if cid not in sessions:sessions[cid]=api('/api/chat/sessions',{})['session_id'];(OUT/'sessions.json').write_text(json.dumps(sessions))
  sid=sessions[cid]
  for turn,case in enumerate(conv['turns'],1):
   if (cid,turn) in done:continue
   assert hashes=={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
   key=(cid,turn);started=time.perf_counter();events=[]
   if key in dispatch:rid=dispatch[key]['request_id'];accepted={'recovered_without_post':True,'record':api(f'/api/chat/sessions/{sid}/messages/{rid}')}
   else:
    rid=uuid.uuid4().hex;d={'conversation':cid,'turn':turn,'session_id':sid,'request_id':rid,'question':case['question'],'at':datetime.now(timezone.utc).isoformat()};append('dispatches.jsonl',d);dispatch[key]=d
    accepted=api(f'/api/chat/sessions/{sid}/messages',{'question':case['question'],'request_id':rid})
   accept_seconds=time.perf_counter()-started
   try:
    with urllib.request.urlopen(f'http://127.0.0.1:8080/api/chat/sessions/{sid}/messages/{rid}/events',timeout=240) as response:
     for line in response:
      e=json.loads(line);events.append({'received_seconds':time.perf_counter()-started,**e})
   except Exception as e:append('client_errors.jsonl',{'conversation':cid,'turn':turn,'request_id':rid,'error':repr(e)})
   deadline=time.monotonic()+240
   while True:
    raw=chat_flow.get(sid,rid)
    if raw['status']!='pending':break
    if time.monotonic()>deadline:raise RuntimeError('아직 pending; 자동 재생성 안 함')
    time.sleep(.2)
   actual=[r['intent_id'] for r in (raw.get('analysis') or {}).get('requests',[])];observed=False;before=True
   for e in events:
    if e['type']=='analysis' or e.get('record',{}).get('analysis_validated'):observed=True
    if e['type']=='delta' and not observed:before=False
   row={'conversation':cid,'turn':turn,'session_id':sid,'request_id':rid,'question':case['question'],'expected_ids':case['expected'],'actual_ids':actual,'id_correct':set(actual)==set(case['expected']),'criteria':case['criteria'],'accepted':accepted,'accept_http_seconds':accept_seconds,'client_total_seconds':time.perf_counter()-started,'validated_before_body':before,'delta_events':sum(e['type']=='delta' for e in events),'events':events,'record':raw};append('runs.jsonl',row);done.add(key)
   print(cid,turn,raw['status'],actual,round(row['client_total_seconds'],3),'calls',raw['llm_call_count'],'deltas',row['delta_events'],flush=True)
 (OUT/'completion.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'n':len(done),'hashes_unchanged':True},indent=2))
if __name__=='__main__':main()
