import json,hashlib,time,threading,uuid,os
from datetime import datetime,timezone
from . import config,llm_client

LOCK=threading.Lock()
MESSAGES={'classification_error':'분류 처리 중 오류가 발생했습니다.'}

def load_taxonomy():
 items=json.loads((config.ROOT/config.TAXONOMY_FILE).read_text())
 if not isinstance(items,list) or [r.get('id') for r in items]!=list(config.INTENT_IDS):raise ValueError('2차정리 15개 ID 순서/누락 오류')
 for row in items:
  if set(row)!={'id','name','description'} or any(not isinstance(row[k],str) or not row[k].strip() for k in row):raise ValueError('분류 정의 필드 오류')
 return items

def versions():return {'taxonomy_version':config.TAXONOMY_VERSION,'prompt_version':config.PROMPT_VERSION,'taxonomy_sha256':hashlib.sha256((config.ROOT/config.TAXONOMY_FILE).read_bytes()).hexdigest(),'prompt_sha256':hashlib.sha256((config.ROOT/config.PROMPT_FILE).read_bytes()).hexdigest()}

def messages(question,items):return [{'role':'system','content':(config.ROOT/config.PROMPT_FILE).read_text()+'\n\n분류 정의:\n'+json.dumps(items,ensure_ascii=False)},{'role':'user','content':question}]

def unique(pairs):
 r={}
 for k,v in pairs:
  if k in r:raise ValueError('중복 JSON 필드')
  r[k]=v
 return r

def validate(content,finish_reason,tool_calls=None):
 if finish_reason in ('length','max_tokens'):raise llm_client.ModelError('truncated','출력 한도에 도달했습니다.')
 if tool_calls:raise ValueError('분류 응답의 tool_calls는 허용되지 않습니다.')
 v=json.loads(content,object_pairs_hook=unique)
 if not isinstance(v,dict) or set(v)!={'intents'}:raise ValueError('intents 필드만 허용됩니다.')
 ids=v['intents']
 if not isinstance(ids,list) or not ids or any(type(x) is not str or x not in config.INTENT_IDS for x in ids):raise ValueError('허용된 ID의 비어 있지 않은 배열이 필요합니다.')
 if len(set(ids))!=len(ids):raise ValueError('중복 의도 ID')
 return v

def classify(backend,question,save=True):
 start=time.perf_counter();r={'request_id':uuid.uuid4().hex,'created_at':datetime.now(timezone.utc).isoformat(),'backend':backend,'question':question,'status':'classification_error','intents':[],'display_message':MESSAGES['classification_error'],'llm_call_count':0,'error_kind':None,'error':None,'reasoning':None,'raw_response':None,'model_http_seconds':None}
 try:
  if not isinstance(question,str) or not question.strip() or len(question)>4000:raise ValueError('질문은 1~4000자여야 합니다.')
  marker=config.ROOT/'data/runtime/evaluation.lock'
  if marker.exists() and not os.getenv('LAM_EVALUATOR_PID'):
   try:
    pid=int(marker.read_text());os.kill(pid,0)
   except (ValueError,ProcessLookupError):pass
   else:raise llm_client.ModelError('evaluation_running','순차 비교 평가 중입니다. 평가 완료 후 실행하세요.')
  targets=config.targets()
  if backend not in targets:raise ValueError('지원하지 않는 실행 구성')
  t=targets[backend];r.update(model=t.model,think_requested=t.think,settings_requested={'temperature':0,'seed':20261012,'output_limit':256,'context_requested':65536 if t.engine=='vllm' else 16384},**versions());items=load_taxonomy()
  with LOCK:
   state=llm_client.probe(t);r['availability']=state
   if not state['can_run']:raise llm_client.ModelError('unavailable',state.get('reason') or '실행 구성이 미가용합니다.')
   wire_messages=messages(question,items);wire_path,wire_payload=llm_client.payload(t,wire_messages);r.update(request=config.clean(wire_payload),endpoint=t.url+wire_path)
   r['llm_call_count']=1;httpstart=time.perf_counter()
   try:r.update(llm_client.infer(t,wire_messages))
   except Exception:
    r['model_http_seconds']=time.perf_counter()-httpstart;raise
   result=validate(r['content'],r['finish_reason'],r['tool_calls']);r.update(result);r['status']='classified'
   by_id={row['id']:row for row in items};r['intent_definitions']=[by_id[i] for i in result['intents']]
   r['display_message']=' · '.join(f"{row['name']} ({row['id']})" for row in r['intent_definitions'])

 except llm_client.ModelError as e:r.update(error_kind=e.kind,error=str(e))
 except (ValueError,TypeError,KeyError):r.update(error_kind='validation_error',error='입력·분류표 또는 모델 출력 형식이 올바르지 않습니다.')
 except Exception:r.update(error_kind='internal_error',error='분류 처리 중 내부 오류가 발생했습니다.')
 r['elapsed_seconds']=time.perf_counter()-start;r=config.clean(r)
 if save:
  path=config.ROOT/'data/runtime/requests-v2.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
  with path.open('a') as f:f.write(json.dumps(r,ensure_ascii=False)+'\n')
 return r
