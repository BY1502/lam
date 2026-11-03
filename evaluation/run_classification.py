"""Exclusive sequential deployment comparison. Restores original container state in finally."""
import argparse,fcntl,hashlib,json,os,signal,subprocess,sys,threading,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend import classifier,config,llm_client
from evaluation.gpu_safety import wait_released,exclusive


def command(args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT).strip()
def inspect(name):return json.loads(command(['docker','inspect',name]))[0]
def fingerprint(v):return hashlib.sha256(json.dumps({'Config':v['Config'],'HostConfig':v['HostConfig'],'Mounts':v['Mounts']},sort_keys=True).encode()).hexdigest()
def state():
 g=[]
 for line in command(['nvidia-smi','--query-gpu=index,uuid,name,memory.used,memory.total','--format=csv,noheader,nounits']).splitlines():
  i,uid,name,used,total=[x.strip() for x in line.split(',')];g.append({'index':i,'uuid':uid,'name':name,'memory_used_mib':int(used),'memory_total_mib':int(total)})
 apps=[]
 for line in command(['nvidia-smi','--query-compute-apps=pid,process_name,gpu_uuid,used_memory','--format=csv,noheader,nounits']).splitlines():
  if not line.strip():continue
  pid,name,uid,used=[x.strip() for x in line.split(',')];apps.append({'pid':pid,'name':name,'gpu_uuid':uid,'memory_mib':used})
 ollama=config.targets()['ollama_qwen'];ollama_running=inspect('ollama')['State']['Running'];return {'at':datetime.now(timezone.utc).isoformat(),'gpus':g,'processes':apps,'ollama_running':ollama_running,'ollama_ps':llm_client.http(ollama,'/api/ps') if ollama_running else {'models':[],'container_stopped':True},'ollama_ps_cli':command(['docker','exec','ollama','ollama','ps']) if ollama_running else 'container stopped','vllm_running':inspect('vllm-qwen')['State']['Running']}

def idle_vllm():
 t=config.targets()['vllm_qwen'];url=t.url.removesuffix('/v1')+'/metrics';req=urllib.request.Request(url,headers={'Authorization':'Bearer '+t.key} if t.key else {})
 with urllib.request.urlopen(req,timeout=5) as f:metrics=f.read().decode()
 gauges=[float(line.split()[-1]) for line in metrics.splitlines() if line.startswith(('vllm:num_requests_running{','vllm:num_requests_waiting{'))]
 if not gauges or any(gauges):raise RuntimeError('vLLM 유휴 상태를 확인할 수 없습니다. 다른 작업을 중단하지 않습니다.')

def no_foreign_gpu():
 allowed=set()
 for name in ['ollama','vllm-qwen']:
  if inspect(name)['State']['Running']:
   for line in command(['docker','top',name,'-eo','pid']).splitlines()[1:]:allowed.add(line.strip())
 foreign=[p for p in state()['processes'] if p['pid'] not in allowed]
 if foreign:raise RuntimeError('평가와 무관한 GPU 작업 감지; 전환 중단')


def main():
 if config.TAXONOMY_VERSION!='food-manufacturing-first-review-v1.0.0':raise RuntimeError('1차 전용 실행기입니다. 2차는 evaluation.run_second_review를 사용하세요.')
 parser=argparse.ArgumentParser();parser.add_argument('--experiment',required=True);args=parser.parse_args()
 if not args.experiment.replace('-','').replace('_','').isalnum():raise ValueError('실험 ID 오류')
 out=ROOT/'evaluation/results'/args.experiment
 if (out/'paused.json').exists():raise RuntimeError('사용자가 보류한 실험입니다. 재개 승인 없이 실행할 수 없습니다.')
 out.mkdir(parents=True,exist_ok=True)
 lockpath=ROOT/'data/runtime/evaluation.lock';lock=lockpath.open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);lock.seek(0);lock.truncate();lock.write(str(os.getpid()));lock.flush()
 os.environ['LAM_EVALUATOR_PID']=str(os.getpid())
 manifestpath=out/'manifest.json';files=['prompts/classification.md','taxonomy/intents.json','taxonomy/intent_classification.md','evaluation/datasets/basic.json','evaluation/datasets/edge.json','evaluation/datasets/variants.json','backend/config.py','backend/classifier.py','backend/llm_client.py','evaluation/gpu_safety.py','evaluation/run_classification.py']
 hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}
 initial={n:inspect(n) for n in ['vllm-qwen','ollama']}
 if not manifestpath.exists():
  initial_state=state()
  if initial_state['ollama_ps']['models']:raise RuntimeError('기존 Ollama 적재 작업이 있어 시작하지 않습니다.')
  no_foreign_gpu()
  if initial['vllm-qwen']['State']['Running']:idle_vllm()
  # Read-only metadata collection; no generation.
  details={}
  for key,t in config.targets().items():
   if t.engine=='ollama':
    try:
     v=llm_client.http(t,'/api/show',{'model':t.model});details[key]={'version':llm_client.http(t,'/api/version'),'thinking':v.get('thinking'),'details':v.get('details'),'model_info':v.get('model_info'),'template_sha256':hashlib.sha256(v.get('template','').encode()).hexdigest(),'parameters':v.get('parameters')}
    except llm_client.ModelError as exc:details[key]={'unavailable_at_initial_snapshot':True,'reason':str(exc),'metadata_later_in_warmup_availability':True}
   else:
    details[key]={'models':llm_client.http(t,'/models')}
    model_dir=Path('/home/yt/vllm/models/Qwen3.8-27B-FP8')
    details[key]['files']={p.name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'metadata':json.loads(p.read_text()) if p.name=='config.json' else None} for p in [model_dir/'config.json',model_dir/'tokenizer_config.json',model_dir/'chat_template.jinja'] if p.exists()}
    revision_file=model_dir/'.cache/huggingface/download/config.json.metadata'
    if revision_file.exists():details[key]['hf_revision_record']=revision_file.read_text().splitlines()[:2]
  manifest={'experiment':args.experiment,'created_at':datetime.now(timezone.utc).isoformat(),'hashes':hashes,'versions':classifier.versions(),'initial_services':{n:{'running':v['State']['Running'],'image':v['Config']['Image'],'image_id':v['Image'],'config_sha256':fingerprint(v),'ports':v['HostConfig']['PortBindings'],'mounts':[{'source':m['Source'],'destination':m['Destination'],'rw':m['RW']} for m in v['Mounts']],'auth_environment_present':any(any(k in e.split('=')[0].upper() for k in ['TOKEN','API_KEY','PASSWORD']) and bool(e.partition('=')[2]) for e in v['Config'].get('Env',[]))} for n,v in initial.items()},'initial_gpu':initial_state,'metadata':config.clean(details),'orders':[['vllm_qwen','ollama_qwen','ollama_gptoss'],['ollama_qwen','ollama_gptoss','vllm_qwen'],['ollama_gptoss','vllm_qwen','ollama_qwen']],'repetitions':3,'unique_questions':{'basic':66,'edge':20,'variants_prepared_not_executed':16},'warm_policy':'one separate fixed preparation classification per block; measured questions B01..B66 E01..E20; prefix cache enabled/uncontrolled, no flush; service transitions recorded','cold_policy':'separate classification cold experiment omitted: engine start and request-triggered load are not equivalent. Transition readiness and warmup load metrics recorded, never pooled with warm latencies.','requested_settings':{k:{'model':t.model,'think':t.think,'temperature':0,'seed':20261012,'output_limit':256,'ctx':65536 if t.engine=='vllm' else 16384} for k,t in config.targets().items()},'restore_commands':['docker start vllm-qwen','docker start ollama']}
  manifestpath.write_text(json.dumps(config.clean(manifest),ensure_ascii=False,indent=2))
 else:
  manifest=json.loads(manifestpath.read_text())
  if manifest['hashes']!=hashes:raise RuntimeError('Frozen source or data changed; new experiment ID required')
 for key in config.targets():(out/key).mkdir(exist_ok=True)
 transitions=out/'transitions.jsonl'
 def log(action,**extra):
  record={'action':action,**extra,'state':state()}
  with transitions.open('a') as f:f.write(json.dumps(config.clean(record),ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
 def wait_vllm():
  start=time.perf_counter()
  for _ in range(360):
   try:
    if llm_client.http(config.targets()['vllm_qwen'],'/models').get('data'):return time.perf_counter()-start
   except (llm_client.ModelError,ConnectionError):pass
   time.sleep(1)
  raise RuntimeError('vLLM 준비 시간 초과')
 def unload_owned():
  if not inspect('ollama')['State']['Running']:return
  t=config.targets()['ollama_qwen'];loaded=llm_client.http(t,'/api/ps')['models']
  owned={x.model for x in config.targets().values() if x.engine=='ollama'}
  if any(x['name'] not in owned for x in loaded):raise RuntimeError('다른 사용자 Ollama 모델 감지; 중단')
  for m in loaded:llm_client.http(t,'/api/chat',{'model':m['name'],'messages':[],'keep_alive':0},timeout=120)
  for _ in range(60):
   if not llm_client.http(t,'/api/ps')['models']:return
   time.sleep(1)
  raise RuntimeError('Ollama 언로드 확인 실패')
 def transition(key):
  no_foreign_gpu();start=time.perf_counter()
  if inspect('vllm-qwen')['State']['Running']:idle_vllm();command(['docker','stop','--time','60','vllm-qwen'])
  unload_owned()
  if inspect('ollama')['State']['Running']:command(['docker','stop','--time','60','ollama'])
  released_state=wait_released(state)
  log('gpu_release_confirmed',backend=key,released_state=released_state)
  if key=='vllm_qwen':
   command(['docker','start','vllm-qwen']);readiness=wait_vllm()
  else:
   command(['docker','start','ollama']);readiness=None
   for _ in range(60):
    try:
     llm_client.http(config.targets()['ollama_qwen'],'/api/ps');break
    except (llm_client.ModelError,ConnectionError):time.sleep(1)
   else:raise RuntimeError('Ollama API 준비 실패')
  exclusive(key,state())
  log('transition_ready',backend=key,transition_seconds=time.perf_counter()-start,api_readiness_seconds=readiness)
 def check_hashes():
  if any(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h for p,h in hashes.items()):raise RuntimeError('입력 해시 변경; 실험 중단')
 def handler(signum,frame):raise KeyboardInterrupt('Signal '+str(signum))
 signal.signal(signal.SIGTERM,handler)
 cases=json.loads((ROOT/'evaluation/datasets/basic.json').read_text())+json.loads((ROOT/'evaluation/datasets/edge.json').read_text())
 completed={}
 for key in config.targets():
  p=out/key/'runs.jsonl';completed[key]={(json.loads(l)['repeat'],json.loads(l)['case']['id']) for l in p.read_text().splitlines()} if p.exists() else set()
 peak={};stop=threading.Event();samples=[];sample_lock=threading.Lock()
 def monitor():
  while not stop.is_set():
   try:
    points=[]
    for line in command(['nvidia-smi','--query-gpu=index,memory.used','--format=csv,noheader,nounits']).splitlines():
     i,m=[x.strip() for x in line.split(',')];points.append((i,int(m)))
    with sample_lock:samples.append((time.perf_counter(),dict(points)))
   except Exception:pass
   stop.wait(.25)
 thread=threading.Thread(target=monitor,daemon=True);thread.start()
 try:
  for repeat,order in enumerate(manifest['orders'],1):
   for key in order:
    todo=[case for case in cases if (repeat,case['id']) not in completed[key]]
    if not todo:continue
    check_hashes();transition(key)
    warm=classifier.classify(key,'포장 설비의 하위 장치가 각각 무슨 일을 하는지 알려줘.')
    with (out/key/'warmups.jsonl').open('a') as f:f.write(json.dumps({'repeat':repeat,'record':warm,'state':state()},ensure_ascii=False)+'\n')
    if warm['llm_call_count']!=1:raise RuntimeError('모델 준비 요청 불가: '+str(warm.get('error')))
    exclusive(key,state())
    if config.targets()[key].engine=='ollama':
     target=config.targets()[key];show=llm_client.http(target,'/api/show',{'model':target.model})
     metadata={'repeat':repeat,'model':target.model,'version':llm_client.http(target,'/api/version'),'thinking':show.get('thinking'),'details':show.get('details'),'model_info':show.get('model_info'),'parameters':show.get('parameters'),'template_sha256':hashlib.sha256(show.get('template','').encode()).hexdigest(),'requested_think':target.think,'same_engine_only':True}
     with (out/key/'server_metadata.jsonl').open('a') as f:f.write(json.dumps(config.clean(metadata),ensure_ascii=False)+'\n')

    ps=state();models=ps['ollama_ps']['models'];offloaded=any(x.get('size_vram',0)<x.get('size',0) for x in models)
    if key.startswith('ollama') and not models:raise RuntimeError('Ollama 적재 확인 실패')
    for case in todo:
     check_hashes();no_foreign_gpu();exclusive(key,state());t0=time.perf_counter();record=classifier.classify(key,case['question']);t1=time.perf_counter()
     with sample_lock:window=[m for ts,m in samples if t0-.5<=ts<=t1+.1]
     gpu={i:max(m[i] for m in window if i in m) for i in ['0','1'] if any(i in m for m in window)}
     violation=None
     try:exclusive(key,state())
     except RuntimeError as exc:violation=str(exc)
     row={'repeat':repeat,'case':case,'record':record,'gpu_peak_mib':gpu,'gpu_sample_period_seconds':.25,'offloaded':offloaded,'performance_excluded':offloaded or bool(violation),'safety_violation':violation,'exclusion_reason':'Ollama size_vram < size (CPU offload)' if offloaded else None,'warm':True}
     with (out/key/'runs.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
     if violation:raise RuntimeError(violation)
     print(repeat,key,case['id'],record['status'],record.get('intent_id'),round(record['elapsed_seconds'],3),record.get('error_kind'),flush=True)
    log('block_complete',repeat=repeat,backend=key);unload_owned()
 finally:
  stop.set();thread.join(timeout=3)
  try:
   unload_owned()
   original=manifest['initial_services']['vllm-qwen']['running']
   if original and not inspect('vllm-qwen')['State']['Running']:
    if inspect('ollama')['State']['Running']:command(['docker','stop','--time','60','ollama'])
    wait_released(state);command(['docker','start','vllm-qwen']);wait_vllm()
   elif not original and inspect('vllm-qwen')['State']['Running']:idle_vllm();command(['docker','stop','--time','60','vllm-qwen'])
   # Preserve the latest strict single-engine policy even if an older initial snapshot had both API containers running.
   desired_ollama=manifest['initial_services']['ollama']['running'] and not original
   if desired_ollama and not inspect('ollama')['State']['Running']:
    wait_released(state);command(['docker','start','ollama'])
   elif not desired_ollama and inspect('ollama')['State']['Running']:
    command(['docker','stop','--time','60','ollama'])
   restoration={'services':{n:{'running':inspect(n)['State']['Running'],'configuration_unchanged':fingerprint(inspect(n))==manifest['initial_services'][n]['config_sha256']} for n in ['vllm-qwen','ollama']},'state':state(),'hashes_unchanged':all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items())}
   (out/'restoration.json').write_text(json.dumps(config.clean(restoration),ensure_ascii=False,indent=2));log('restored')
  finally:
   fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
 print('FINISHED',out,flush=True)
if __name__=='__main__':main()
