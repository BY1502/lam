"""Accepted/idempotent chat messages → validated analysis → genuine response stream.
No business data, tools, approval execution or model-driven SQL is present here.
"""
import copy,json,os,threading,time,uuid,hashlib,re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from . import chatbot as store,classifier,config,llm_client,chat_flow_schema as spec,chat_analysis,mock_catalog
from .chat_contract import ContractError

POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='chat')
JOBS={};JOBS_LOCK=threading.Lock()
TERMINAL={'completed','error','interrupted','ok','chat_error'}
class RequestConflict(Exception):pass

class Job:
 def __init__(self,r,prior):
  self.accepted_monotonic=time.perf_counter();self.record=r;self.prior=prior;self.cancel=threading.Event();self.condition=threading.Condition();self.events=deque(maxlen=512);self.seq=0;self.ended=False;self.cancel_reason=None
 def emit(self,kind,**values):
  with self.condition:
   self.seq+=1;event={'type':kind,'session_id':self.record['session_id'],'request_id':self.record['request_id'],'seq':self.seq,**values};self.events.append(event);self.condition.notify_all()
 def check(self):
  if self.cancel.is_set():raise llm_client.ModelError('cancelled',self.cancel_reason or '사용자가 요청을 중단했습니다.')

def versions():
 return {**store.version_info(),'chat_flow_version':spec.VERSION,'chat_prompt_version':spec.PROMPT_VERSION,'chat_schema_version':spec.VERSION,'validation_contract_version':spec.VERSION,'memory_version':spec.MEMORY_VERSION,'response_prompt_version':spec.REPLY_VERSION,'chat_prompt_sha256':sha('prompts/chat_analysis_v3.md'),'response_prompt_sha256':sha('prompts/chat_response_v3.md'),'analysis_schema_sha256':hashlib.sha256(json.dumps(spec.SCHEMA,sort_keys=True).encode()).hexdigest()}
def sha(path):return hashlib.sha256((config.ROOT/path).read_bytes()).hexdigest()
def public(r):
 def safe(v):
  if isinstance(v,dict):return {k:safe(x) for k,x in v.items() if k.lower() not in {'authorization','api_key','key','reasoning','reasoning_content','thinking','raw_content'}}
  if isinstance(v,list):return [safe(x) for x in v]
  return v
 value=copy.deepcopy(r)
 if value.get('chat_flow_version') and not value.get('analysis_validated'):
  value.pop('analysis_stage',None)
  if value.get('status')=='error':
   failure=chat_analysis.failure_details(r)
   if failure:value['analysis_failure']=failure
  else:value.pop('analysis_failure',None)
 return safe(config.clean(value))
def get(sid,rid):
 with store.db() as c:
  row=c.execute('SELECT record_json FROM turns WHERE request_id=? AND session_id=?',(rid,sid)).fetchone()
 if not row:raise store.SessionMissing()
 return json.loads(row[0])

def sessions(limit=50,offset=0):
 with store.db() as c:
  rows=c.execute('''SELECT s.session_id,s.created_at,count(t.request_id) AS messages,
    max(t.created_at) AS last_at FROM sessions s LEFT JOIN turns t ON t.session_id=s.session_id
    GROUP BY s.session_id ORDER BY coalesce(max(t.created_at),s.created_at) DESC,s.session_id LIMIT ? OFFSET ?''',(limit,offset)).fetchall()
 return [dict(r) for r in rows]

def submit(sid,question,rid):
 if not re.fullmatch('[0-9a-f]{32}',rid):raise ValueError('request_id는 UUID hex 형식이어야 합니다.')
 if type(question) is not str or not question.strip() or len(question)>4000:raise ValueError('질문은 공백이 아닌 1~4000자여야 합니다.')
 with JOBS_LOCK:
  with store.db() as c:
   c.execute('BEGIN IMMEDIATE')
   old=c.execute('SELECT record_json FROM turns WHERE request_id=?',(rid,)).fetchone()
   if old:
    r=json.loads(old[0])
    if r['session_id']!=sid or r['question']!=question:raise RequestConflict('같은 요청 ID를 다른 세션/원문에 사용할 수 없습니다.')
    return r,True
   if not c.execute('SELECT 1 FROM sessions WHERE session_id=?',(sid,)).fetchone():raise store.SessionMissing()
   if c.execute("SELECT 1 FROM turns WHERE session_id=? AND status='pending'",(sid,)).fetchone():raise store.SessionBusy()
   prior=[json.loads(x[0]) for x in c.execute('SELECT record_json FROM turns WHERE session_id=? ORDER BY rowid',(sid,)).fetchall()]
   r={'session_id':sid,'request_id':rid,'message_id':uuid.uuid4().hex,'message_order':len(prior)+1,'created_at':store.now(),'accepted_at':store.now(),'question':question,'status':'pending','phase':'accepted','reply':'','analysis':None,'analysis_validated':False,'response_generation_allowed':False,'mock_mode':'mock','tool_catalog_version':mock_catalog.load()['version'],'tool_catalog_sha256':sha('settings/mock_tools.json'),'request_states':[],'llm_call_count':0,'response_call_count':0,'analysis_call_count':0,'backend':'vllm_qwen','model':config.targets()['vllm_qwen'].model,'think_requested':False,'settings_requested':{'temperature':0,'seed':20261012,'analysis_output_limit':spec.ANALYSIS_TOKENS,'response_output_limit':spec.REPLY_TOKENS},'validation_checks':{'format':'not_checked','source':'not_checked','meaning':'not_automatically_verified'},'timings':{},'error':None,'error_kind':None,'error_stage':None,'review_status':'unreviewed','is_gold_label':False,'actual_tool_calls':0,'execution_facts':{'business_actions_performed':False,'verified_results_available':False,'system_generated_concrete_proposals_available':False,'approval_or_application_performed':False},**versions()}
   c.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',(rid,sid,r['created_at'],question,'pending',json.dumps(config.clean(r),ensure_ascii=False)))
  accepted=copy.deepcopy(r);job=Job(r,prior);JOBS[rid]=job;POOL.submit(process,job)
  return accepted,False

def analysis_messages(r,prior):
 previous,limits,sources=store.history_context(prior,session_id=r['session_id'])
 sources[r['request_id']]={'text':r['question'],'session_id':r['session_id'],'role':'user'}
 memory,mlimits=chat_analysis.bounded_memory(prior,sources)
 interpretations=[];chars=0
 for old in reversed(prior):
  if old['request_id'] not in sources or not (old.get('analysis_validated') or old.get('status')=='ok'):continue
  item={'turn_id':old['request_id'],'requests':[{k:v for k,v in req.items() if k in ['intent_id','interpreted_request','target_expressions','time_expression','conditions','evidence']} for req in (old.get('analysis') or {}).get('requests',[])],'source_type':'model_interpretation','semantic_verified':False}
  size=len(json.dumps(item,ensure_ascii=False))
  if chars+size>4000:continue
  interpretations.append(item);chars+=size
 limits.update(memory_limits=mlimits,interpretation_characters=chars,max_interpretation_characters=4000,interpretation_turn_ids=[x['turn_id'] for x in interpretations],business_actions_performed=False,verified_business_results_available=False,concrete_system_proposals_available=False)
 system=(config.ROOT/'prompts/chat_analysis_v3.md').read_text()+'\n분류 정의:\n'+json.dumps(store.load_taxonomy(),ensure_ascii=False)+'\n전달된 해석·활성 맥락(업무 사실 아님):\n'+json.dumps({'active_memory':memory,'previous_interpretations':list(reversed(interpretations)),'limits':limits},ensure_ascii=False)
 msgs=[{'role':'system','content':system},*[x for x in previous if x['role']=='user'],{'role':'user','content':json.dumps({'turn_id':r['request_id'],'text':r['question'],'source_type':'user_statement'},ensure_ascii=False)}]
 return msgs,limits,sources,memory

def response_messages(r):
 # Defense in depth: never forward out-of-scope requests or original compound text.
 allowed=[{k:v for k,v in req.items() if k!='evidence'} for req in r['analysis']['requests'] if req['intent_id'] in config.INTENT_IDS and req['intent_id']!='out_of_scope']
 if not r['analysis_validated'] or not allowed:raise ContractError('gate_error','응답 생성 조건을 만족하지 않습니다.')
 tools=[{'request_index':x['request_index'],'candidates':[{'id':t['id'],'name':t['name'],'description':t['description'],'mode':'mock','implemented':False,'execution_enabled':False,'write_enabled':False,'approval_required':t['approval_required'],'approved':False} for t in x['candidates']]} for x in r['mock_tools']['requests'] if x['status']=='valid']
 # Resolved requests carry the needed prior targets/conditions. No raw conversation or hypothetical result is forwarded.
 context={'validated_requests':allowed,'active_memory':r['memory_snapshot'],'mock_tools':tools,'business_actions_performed':False,'verified_business_results_available':False,'concrete_system_proposals_available':False,'context_policy':'검증된 요청 해석과 활성 슬롯만; 원문 대화·범위 밖 요청·실제 결과 없음'}
 return [{'role':'system','content':(config.ROOT/'prompts/chat_response_v3.md').read_text()},{'role':'user','content':json.dumps(context,ensure_ascii=False)}]

def checkpoint(job):store.save_record(job.record)
def gate(r):
 if not r.get('analysis_validated'):return False
 return any(x['intent_id'] in config.INTENT_IDS and x['intent_id']!='out_of_scope' for x in r['analysis']['requests'])

def process(job):
 r=job.record;start=job.accepted_monotonic;response_start=None;analysis_start=None;guard='';stage='analysis'
 try:
  while not classifier.LOCK.acquire(timeout=.1):job.check()
  try:
   job.check();r['timings']['queue_seconds']=time.perf_counter()-start
   marker=config.ROOT/'data/runtime/evaluation.lock'
   if marker.exists() and not os.getenv('LAM_EVALUATOR_PID'):
    try:os.kill(int(marker.read_text()),0)
    except (ValueError,ProcessLookupError):pass
    else:raise llm_client.ModelError('evaluation_running','기존 분류 평가가 진행 중입니다.')
   target=config.targets()['vllm_qwen'];r['availability']=llm_client.probe(target)
   if not r['availability']['can_run']:raise llm_client.ModelError('unavailable',r['availability'].get('reason') or '모델 연결 불가')
   job.check();r['phase']='intent';checkpoint(job);job.emit('status',phase='intent',message='의도 분석 중')
   msgs,limits,sources,memory=analysis_messages(r,job.prior);r['context_limits']=limits;r['memory_previous']=copy.deepcopy(memory)
   path,body=llm_client.payload(target,msgs,schema=spec.SCHEMA,max_tokens=spec.ANALYSIS_TOKENS)
   r.update(request=config.clean(body),endpoint=target.url+path,analysis_call_count=1,llm_call_count=1);checkpoint(job)
   analysis_start=time.perf_counter()
   try:raw=llm_client.infer(target,msgs,schema=spec.SCHEMA,max_tokens=spec.ANALYSIS_TOKENS)
   finally:r['timings']['analysis_seconds']=time.perf_counter()-analysis_start
   r['analysis_stage']=raw;r['model_http_seconds']=raw['model_http_seconds'];checkpoint(job);job.check()
   a,active=chat_analysis.validate(raw['content'],raw['finish_reason'],raw['tool_calls'],sources,session_id=r['session_id'],current_turn_id=r['request_id'],prior_memory=memory)
   r.update(analysis=a,analysis_validated=True,memory_snapshot=active,memory_previous=memory,memory_update=a['memory'])
   r['validation_checks'].update(format='passed',source='passed');r['mock_tools']=mock_catalog.connections(a['requests']);r['request_states']=[{'request_index':i,'intent_id':x['intent_id'],'status':'out_of_scope' if x['intent_id']=='out_of_scope' else 'clarify' if x['intent_id']=='clarify' else 'valid'} for i,x in enumerate(a['requests'])]
   labels={x['id']:x['name'] for x in store.load_taxonomy()};r['intent_labels']=[{'id':x['intent_id'],'name':labels[x['intent_id']]} for x in a['requests']]
   r['response_generation_allowed']=gate(r);r['phase']='analysis_validated';checkpoint(job)
   job.emit('analysis',llm_call_count=r['llm_call_count'],analysis_call_count=r['analysis_call_count'],response_call_count=r['response_call_count'],analysis=a,memory_snapshot=active,mock_tools=r['mock_tools'],request_states=r['request_states'],intent_labels=r['intent_labels'],timings=r['timings'])
   if not gate(r):
    r.update(status='completed',phase='complete',status_message='지원하는 업무 의도를 찾지 못했습니다.')
   else:
    job.check();stage='response';msgs=response_messages(r);path,body=llm_client.response_payload(target,msgs,max_tokens=spec.REPLY_TOKENS)
    r.update(phase='response',response_call_count=1,llm_call_count=2,response_stage={'request':config.clean(body),'endpoint':target.url+path,'content':'','usage':{},'finish_reason':None});checkpoint(job);job.emit('status',phase='response',llm_call_count=r['llm_call_count'],message='응답 중')
    response_start=time.perf_counter();last_save=response_start;last_len=0
    for event in llm_client.stream_response(target,msgs,max_tokens=spec.REPLY_TOKENS,cancel=job.cancel):
     job.check()
     if event['type']=='delta':
      r['response_stage']['raw_content']=r['response_stage'].get('raw_content','')+event['text']
      guard+=event['text']
      if '<think' in guard.lower() or '</think' in guard.lower():raise llm_client.ModelError('unexpected_reasoning','본문 채널에 내부 추론 표식이 있어 중단했습니다.')
      # Retain at most a delimiter-length tail so a split reasoning marker never leaks.
      if len(guard)<=9:continue
      visible,guard=guard[:-9],guard[-9:]
      if not r['reply'] and visible.lstrip().startswith('{'):raise llm_client.ModelError('response_format_error','응답 단계가 설명 대신 JSON을 출력했습니다.')
      if 'first_fragment_seconds' not in r['timings']:r['timings'].update(first_fragment_seconds=time.perf_counter()-start,response_first_fragment_seconds=time.perf_counter()-response_start)
      with job.condition:r['reply']+=visible;job.emit('delta',text=visible)
      if time.perf_counter()-last_save>.35 or len(r['reply'])-last_len>=128:checkpoint(job);last_save=time.perf_counter();last_len=len(r['reply'])
     elif event['type']=='end':
      r['response_stage'].update(finish_reason=event['finish_reason'],usage=event['usage'],model_http_seconds=event['model_http_seconds'])
      if guard:
       if 'first_fragment_seconds' not in r['timings']:r['timings'].update(first_fragment_seconds=time.perf_counter()-start,response_first_fragment_seconds=time.perf_counter()-response_start)
       with job.condition:r['reply']+=guard;job.emit('delta',text=guard);guard=''
      if event['finish_reason'] in ('length','max_tokens'):raise llm_client.ModelError('truncated','응답 출력 한도에 도달했습니다. 부분 답변을 보존합니다.')
      if event['finish_reason']!='stop':raise llm_client.ModelError('response_finish_error','응답의 정상 종료를 확인하지 못했습니다.')
    if not r['reply'].strip():raise llm_client.ModelError('empty_response','응답 본문이 비어 있습니다.')
    r.update(status='completed',phase='complete')
  finally:classifier.LOCK.release()
 except ContractError as e:
  r.update(status='error',phase='error',error_kind=e.kind,error_stage='source' if e.kind=='source_error' else 'format',error=str(e),response_generation_allowed=False)
  r['validation_checks'].update(format='passed' if e.kind=='source_error' else 'failed',source='failed' if e.kind=='source_error' else 'not_checked')
 except llm_client.ModelError as e:
  if stage=='analysis' and e.kind=='truncated':r['validation_checks']['format']='failed'
  r.update(status='interrupted' if e.kind=='cancelled' else 'error',phase='interrupted' if e.kind=='cancelled' else 'error',error_kind=e.kind,error_stage=stage,error=str(e))
 except Exception:
  r.update(status='error',phase='error',error_kind='internal_error',error_stage=stage,error='챗봇 처리 중 내부 오류가 발생했습니다. 자동 재시도하지 않습니다.')
 finally:
  if r['status']=='error' and not r.get('analysis_validated'):
   failure=chat_analysis.failure_details(r)
   if failure:r['analysis_failure']=failure
  if response_start:
   r['timings']['response_seconds']=time.perf_counter()-response_start
   r['response_stage']['content']=r['reply']
  r['timings']['total_seconds']=time.perf_counter()-start;r.update(elapsed_seconds=r['timings']['total_seconds'],completed_at=store.now())
  r['analysis_http_seconds']=(r.get('analysis_stage') or {}).get('model_http_seconds')
  r['response_http_seconds']=(r.get('response_stage') or {}).get('model_http_seconds')
  http_values=([r['analysis_http_seconds']] if r['analysis_call_count'] else [])+([r['response_http_seconds']] if r['response_call_count'] else [])
  r['model_http_seconds']=sum(http_values) if http_values and all(isinstance(x,(int,float)) for x in http_values) else None
  for prefix,usage_key in [('input','prompt_tokens'),('output','completion_tokens')]:
   values=[]
   if r['analysis_call_count']:values.append((r.get('analysis_stage') or {}).get(prefix+'_tokens'))
   if r['response_call_count']:values.append(((r.get('response_stage') or {}).get('usage') or {}).get(usage_key))
   r[prefix+'_tokens']=sum(values) if values and all(isinstance(v,int) for v in values) else None
  store.save_record(r,True)
  job.emit('complete' if r['status']=='completed' else 'error',record=public(r))
  with job.condition:job.ended=True;job.condition.notify_all()
  # Jobs carry only a bounded in-memory event replay; finished requests are read from SQLite.
  with JOBS_LOCK:
   for rid,old in list(JOBS.items()):
    if old.ended and rid!=r['request_id']:JOBS.pop(rid,None)

def cancel(sid,rid,reason='사용자 중단 요청'):
 r=get(sid,rid)
 if r['status'] in TERMINAL:return public(r)
 with JOBS_LOCK:job=JOBS.get(rid)
 if job and not job.ended:
  # Persist only cancellation metadata against the latest pending row. A stale
  # whole-record checkpoint must never overwrite a worker's terminal commit.
  requested_at=store.now()
  with store.db() as c:
   c.execute('BEGIN IMMEDIATE')
   current=json.loads(c.execute('SELECT record_json FROM turns WHERE request_id=? AND session_id=?',(rid,sid)).fetchone()[0])
   if current['status'] in TERMINAL:return public(current)
   current.update(cancel_requested_at=requested_at,cancel_reason=reason)
   c.execute("UPDATE turns SET record_json=? WHERE request_id=? AND session_id=? AND status='pending'",(json.dumps(config.clean(current),ensure_ascii=False),rid,sid))
  with job.condition:job.record['cancel_requested_at']=requested_at;job.record['cancel_reason']=reason;job.cancel_reason=reason;job.cancel.set()
 return public(get(sid,rid))

def events(sid,rid,after=0):
 r=get(sid,rid)
 with JOBS_LOCK:job=JOBS.get(rid)
 if job:
  with job.condition:r=copy.deepcopy(job.record);after=job.seq
 finished=False
 try:
  yield json.dumps({'type':'snapshot','session_id':sid,'request_id':rid,'seq':after,'record':public(r)},ensure_ascii=False)+'\n'
  if r['status'] in TERMINAL:finished=True;return
  if not job:yield json.dumps({'type':'error','session_id':sid,'request_id':rid,'seq':after+1,'record':public(r)},ensure_ascii=False)+'\n';finished=True;return
  while True:
   with job.condition:
    available=[x for x in job.events if x['seq']>after]
    if not available and not job.ended:job.condition.wait(1)
    if not available and job.ended:finished=True;return
   if not available:yield json.dumps({'type':'heartbeat','session_id':sid,'request_id':rid,'seq':after})+'\n';continue
   if after and available[0]['seq']>after+1:
    # Ring-buffer gap: authoritative saved state, then future genuine deltas only.
    with job.condition:after=job.seq;current=copy.deepcopy(job.record)
    yield json.dumps({'type':'snapshot','session_id':sid,'request_id':rid,'seq':after,'record':public(current)},ensure_ascii=False)+'\n';continue
   for event in available:
    after=event['seq'];yield json.dumps(event,ensure_ascii=False)+'\n'
    if event['type'] in ('complete','error'):finished=True;return
 finally:
  if not finished:cancel(sid,rid,'브라우저 응답 연결 중단')

def export(sid):
 for r in store.session_history(sid):
  yield json.dumps(public({'export_version':'chat-training-review-v1.0.0','session_id':sid,'message_id':r.get('message_id'),'request_id':r['request_id'],'message_order':r.get('message_order'),'user_text':r['question'],'record':public(r),'model_labels':(r.get('analysis') or {}).get('requests'),'labels_are_gold':False,'review_status':'unreviewed','human_label':None,'mock_mode':r.get('mock_tools',{}).get('mode'),'schema_origin':r.get('chat_flow_version') or r.get('chat_schema_version') or 'legacy_unversioned'}),ensure_ascii=False)+'\n'

def shutdown():
 with JOBS_LOCK:
  for job in JOBS.values():
   if not job.ended:job.cancel_reason='서버 종료';job.cancel.set()
