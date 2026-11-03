"""Read-only-to-app evaluation using existing chatbot HTTP routes. No generation retry."""
import argparse,fcntl,hashlib,json,os,random,sys,time,urllib.request,urllib.error,subprocess
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend import chatbot,chat_schema,config,llm_client
from evaluation.run_classification import state,inspect,fingerprint,idle_vllm,no_foreign_gpu
from evaluation.gpu_safety import exclusive
OUT=ROOT/'evaluation/results/chatbot-eval-20261029-01'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(config.clean(v),ensure_ascii=False,indent=2)+'\n')
def append(p,v):
 with p.open('a') as f:f.write(json.dumps(config.clean(v),ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
def read(p):return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
def api(path,body=None):
 req=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=240) as f:return json.load(f)
def blank_error(kind,text):return {'status':'chat_error','analysis':None,'reply':None,'error_kind':kind,'error':text,'elapsed_seconds':None,'model_http_seconds':None,'llm_call_count':None}

def main():
 p=argparse.ArgumentParser();p.add_argument('--resume',action='store_true');args=p.parse_args();OUT.mkdir(exist_ok=True,parents=True)
 lock=(ROOT/'data/runtime/chatbot-current-evaluation.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  target=config.targets()['vllm_qwen']
  if not llm_client.probe(target)['can_run']:raise RuntimeError('기존 vLLM 챗봇 연결 불가; 앱/서버를 수정하지 않음')
  api('/api/health');exclusive('vllm_qwen',state());no_foreign_gpu();idle_vllm()
  gold=json.loads((OUT/'questions_gold.json').read_text());cases=gold['cases'];byid={c['id']:c for c in cases}
  files=[str(p.relative_to(ROOT)) for folder in ['backend','frontend'] for p in sorted((ROOT/folder).rglob('*')) if p.is_file() and p.suffix in ['.py','.js','.css','.html']]
  files+=['prompts/chatbot_v2.md','prompts/classification_v2.md','taxonomy/intents_v2_ko.json','taxonomy/intents_v2.json','evaluation/chatbot_current/run.py','evaluation/chatbot_current/make_gold.py','evaluation/results/chatbot-eval-20261029-01/questions_gold.json']
  hashes={f:sha(ROOT/f) for f in files};mp=OUT/'manifest.json'
  if mp.exists():
   if not args.resume:raise RuntimeError('기존 실험을 덮어쓰지 않음. 재개는 --resume')
   manifest=json.loads(mp.read_text())
   if hashes!=manifest['hashes']:raise RuntimeError('고정 코드/입력 변경, 재개하지 않음')
  else:
   container=inspect('vllm-qwen');modeldir=Path('/home/yt/vllm/models/Qwen3.8-27B-FP8');mc=json.loads((modeldir/'config.json').read_text());qc=mc.get('quantization_config') or {}
   blocks=[]
   for case in cases:
    if case['suite']!='conversation':blocks.append([case['id']])
   for n in range(1,11):blocks.append([f'C{n:02d}-T{t}' for t in [1,2,3]])
   orders={str(rep):[iid for block in random.Random(20261029+rep).sample(blocks,len(blocks)) for iid in block] for rep in [1,2,3]}
   revision=modeldir/'.cache/huggingface/download/config.json.metadata'
   manifest={'experiment':OUT.name,'kind':'chatbot_evaluation','created_at':datetime.now(timezone.utc).isoformat(),'versions':chatbot.version_info(),'hashes':hashes,'gold_sha256':sha(OUT/'questions_gold.json'),'model':{'served_model_api':llm_client.http(target,'/models'),'server_version':json.loads(subprocess.check_output(['curl','-sf','http://127.0.0.1:8001/version'],text=True)),'checkpoint_path':str(modeldir),'checkpoint_revision':revision.read_text() if revision.exists() else None,'architecture':mc.get('architectures'),'quantization':{k:v for k,v in qc.items() if k!='modules_to_not_convert'},'model_file_stats':{p.name:{'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in modeldir.glob('*') if p.is_file()},'config_hashes':{p.name:sha(p) for p in modeldir.glob('*') if p.is_file() and p.suffix in ['.json','.jinja']},'weight_shard_sha256':'미측정: 실제 경로·revision·파일크기/mtime를 기록. 가중치 내용을 변경하지 않음.','image':container['Config']['Image'],'image_id':container['Image'],'container_config_sha256':fingerprint(container),'server_cmd':container['Config'].get('Cmd'),'gpu_device_requests':container['HostConfig'].get('DeviceRequests')},'requested_options':llm_client.payload(target,[],schema=chat_schema.SCHEMA,max_tokens=chat_schema.MAX_TOKENS)[1],'initial_state':state(),'repeat_orders':orders,'repeats':3,'unique_question_turns':138,'unique_conversations':10,'evaluation_requests_planned':414,'scored_requests_planned':405,'policy_requests_planned':9,'warmups_planned':1,'ui_requests_planned':4,'total_generation_calls_planned':419,'warmup_question':'지금 저장된 집수조 표시 온도를 확인하고 싶어.','cache_policy':'현재 prefix cache 설정 그대로, 초기화/서버 재시작 없음. 블록 셔플, 대화 내부3턴 순서 고정. 세 반복 동일 모델 상주.','parallel_policy':'평가 요청 순차 실행, Ollama 중지 유지. 앱 프로세스 모델 lock 그대로 사용. 별도 UI 접속은 잠그지 않으며 동시 사용 유무는 기록/제한으로 보고.','streaming':False,'timing_definition':'backend elapsed_seconds는 앱 함수 진입→가용성/모델lock/HTTP/검증 종료, 감사 저장 전. model_http_seconds는 생성POST→응답수신. client_http_seconds는 HTTP API 전송→JSON수신(파일저장 포함), UI와 구분.','review_policy':'문자/ID 자동검사와 전체 생성 문장 AI 의미검토 분리. 내부 thinking은 검토하지 않음. 형식 오류의 raw content도 언어 검토 자료로 보존.','app_modified':False,'question_gold_sent_to_model':False}
   dump(mp,manifest)
   for name in ['backend/chatbot.py','backend/chat_schema.py','prompts/chatbot_v2.md','taxonomy/intents_v2_ko.json']:
    dest=OUT/'input_snapshot'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/name).read_bytes())
  def check():
   if any(sha(ROOT/f)!=h for f,h in manifest['hashes'].items()):raise RuntimeError('앱/입력 해시 변경, 평가 중단')
   if fingerprint(inspect('vllm-qwen'))!=manifest['model']['container_config_sha256']:raise RuntimeError('모델 서버 설정 변경')
   exclusive('vllm_qwen',state());no_foreign_gpu()
  sessions_path=OUT/'sessions.json';sessions=json.loads(sessions_path.read_text()) if sessions_path.exists() else {}
  def sid(key):
   if key not in sessions:sessions[key]=api('/api/chat/sessions',{})['session_id'];dump(sessions_path,sessions)
   return sessions[key]
  if not (OUT/'warmups.jsonl').exists():
   key='warmup';session=sid(key);append(OUT/'warmup_dispatch.jsonl',{'session_id':session,'question':manifest['warmup_question'],'at':datetime.now(timezone.utc).isoformat()});r=api('/api/chat/sessions/'+session+'/messages',{'question':manifest['warmup_question']});append(OUT/'warmups.jsonl',{'record':r,'state':state()})
   if r['status']!='ok':raise RuntimeError('워밍업 실패, 원본 보존. 재시도하지 않음')
  elif read(OUT/'warmups.jsonl')[0]['record']['status']!='ok':raise RuntimeError('기존 워밍업 실패, 재시도 안 함')
  done={(r['repeat'],r['case_id']) for r in read(OUT/'runs.jsonl')}
  dispatched={(r['repeat'],r['case_id']):r for r in read(OUT/'dispatches.jsonl')}
  for repeat in [1,2,3]:
   check()
   for iid in manifest['repeat_orders'][str(repeat)]:
    if (repeat,iid) in done:continue
    case=byid[iid];key=f"r{repeat}:"+(case.get('conversation_id') or iid);session=sid(key);check();started=time.perf_counter();record=None;client_error=None;recovered=False
    if (repeat,iid) in dispatched:
     # A dispatched generation is never sent again, even after interruption.
     history=api('/api/chat/sessions/'+session)['turns'];found=[r for r in history if r['question']==case['question']]
     deadline=time.monotonic()+240
     while found and found[-1]['status']=='pending' and time.monotonic()<deadline:
      time.sleep(1);history=api('/api/chat/sessions/'+session)['turns'];found=[r for r in history if r['question']==case['question']]
     record=found[-1] if found and found[-1]['status']!='pending' else blank_error('dispatch_unresolved','이전 전송의 완료를 확인할 수 없음. 재전송하지 않음');recovered=True
    else:
     dispatch={'repeat':repeat,'case_id':iid,'session_id':session,'question':case['question'],'sent_at':datetime.now(timezone.utc).isoformat()};append(OUT/'dispatches.jsonl',dispatch);dispatched[(repeat,iid)]=dispatch
     try:record=api('/api/chat/sessions/'+session+'/messages',{'question':case['question']})
     except Exception as exc:
      client_error=type(exc).__name__+': '+str(exc)
      # GET only, recover the server's original record without regenerating.
      try:
       history=api('/api/chat/sessions/'+session)['turns'];found=[r for r in history if r['question']==case['question']];record=found[-1] if found and found[-1]['status']!='pending' else blank_error('client_transport_error',client_error)
      except Exception:record=blank_error('client_transport_error',client_error)
    elapsed=None if recovered else time.perf_counter()-started
    row={'repeat':repeat,'case_id':iid,'suite':case['suite'],'conversation_id':case.get('conversation_id'),'turn':case.get('turn',1),'session_id':session,'record':record,'client_http_seconds':elapsed,'client_error':client_error,'recovered_without_generation':recovered,'captured_at':datetime.now(timezone.utc).isoformat()};append(OUT/'runs.jsonl',row);done.add((repeat,iid))
    print(repeat,iid,record['status'],[x['intent_id'] for x in (record.get('analysis') or {}).get('requests',[])],round(record['elapsed_seconds'],3) if record.get('elapsed_seconds') is not None else None,record.get('error_kind'),flush=True)
   append(OUT/'repeat_completion.jsonl',{'repeat':repeat,'n':len(done),'state':state()})
  check();dump(OUT/'completion.json',{'at':datetime.now(timezone.utc).isoformat(),'completed_evaluation_requests':len(done),'hashes_unchanged':True,'final_state':state(),'model_container_configuration_unchanged':True});print('COMPLETE',len(done),flush=True)
 finally:fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
if __name__=='__main__':main()
