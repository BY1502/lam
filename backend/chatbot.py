"""Local session memory + one-pass intent explanation; no work/tool execution imports."""
import hashlib,json,os,sqlite3,time,uuid
from contextlib import contextmanager
from datetime import datetime,timezone
from . import classifier,config,llm_client,chat_schema as spec
from .chat_contract import validate,ContractError

DB_PATH=config.ROOT/'data/chatbot/sessions.sqlite3'
LOG_PATH=config.ROOT/'data/chatbot/requests.jsonl'
ERROR_MESSAGE='의도 분석 중 오류가 발생했습니다. 다시 질문할 수 있습니다.'
class SessionMissing(Exception):pass
class SessionBusy(Exception):pass

def now():return datetime.now(timezone.utc).isoformat()
@contextmanager
def db():
 DB_PATH.parent.mkdir(parents=True,exist_ok=True)
 conn=sqlite3.connect(DB_PATH,timeout=15);conn.row_factory=sqlite3.Row
 conn.executescript('''CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY,created_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS turns(request_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,created_at TEXT NOT NULL,question TEXT NOT NULL,status TEXT NOT NULL,record_json TEXT NOT NULL,FOREIGN KEY(session_id) REFERENCES sessions(session_id));
 CREATE INDEX IF NOT EXISTS session_turns ON turns(session_id,created_at);''')
 try:
  with conn:yield conn
 finally:conn.close()

def load_taxonomy():
 """Korean editorial copy for chat only; original test definitions stay frozen."""
 items=json.loads((config.ROOT/spec.TAXONOMY_FILE).read_text())
 original=classifier.load_taxonomy()
 if not isinstance(items,list) or any(type(x) is not dict or set(x)!={'id','name','description'} or any(type(v) is not str or not v.strip() for v in x.values()) for x in items):raise ValueError('챗봇 분류 정의 형식 오류')
 if [(x['id'],x['name']) for x in items]!=[(x['id'],x['name']) for x in original]:raise ValueError('챗봇 정의 ID·명칭이 원본과 다릅니다.')
 return items

def version_info():
 return {'taxonomy_version':spec.TAXONOMY_VERSION,'taxonomy_sha256':hashlib.sha256((config.ROOT/spec.TAXONOMY_FILE).read_bytes()).hexdigest(),'source_taxonomy_version':config.TAXONOMY_VERSION,'source_taxonomy_sha256':hashlib.sha256((config.ROOT/config.TAXONOMY_FILE).read_bytes()).hexdigest(),'chat_prompt_version':spec.PROMPT_VERSION,'chat_prompt_sha256':hashlib.sha256((config.ROOT/'prompts/chatbot_v2.md').read_bytes()).hexdigest(),'chat_policy_version':spec.POLICY_VERSION,'chat_schema_version':spec.SCHEMA_VERSION,'validation_contract_version':spec.CONTRACT_VERSION}

def create_session():
 sid=uuid.uuid4().hex;created=now()
 with db() as conn:conn.execute('INSERT INTO sessions VALUES(?,?)',(sid,created))
 return {'session_id':sid,'created_at':created,**version_info()}

def session_history(sid):
 with db() as conn:
  if not conn.execute('SELECT 1 FROM sessions WHERE session_id=?',(sid,)).fetchone():raise SessionMissing()
  rows=conn.execute('SELECT record_json FROM turns WHERE session_id=? ORDER BY rowid',(sid,)).fetchall()
 return [json.loads(row[0]) for row in rows]

def save_record(r,final=False):
 clean=config.clean(r)
 with db() as conn:conn.execute('UPDATE turns SET status=?,record_json=? WHERE request_id=? AND session_id=?',(clean['status'],json.dumps(clean,ensure_ascii=False),r['request_id'],r['session_id']))
 if final:
  LOG_PATH.parent.mkdir(parents=True,exist_ok=True)
  with LOG_PATH.open('a') as f:f.write(json.dumps(clean,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
 return clean

def recover_interrupted():
 with db() as conn:pending=conn.execute("SELECT record_json FROM turns WHERE status='pending'").fetchall()
 for row in pending:
  r=json.loads(row[0])
  if r.get('chat_flow_version'):
   r.update(status='interrupted',phase='interrupted',error_kind='server_restarted',error_stage=r.get('phase'),error='서버 재시작으로 중단되었습니다. 저장된 부분 답변을 보존하며 자동 재생성하지 않습니다.',completed_at=now())
  else:r.update(status='chat_error',error_kind='interrupted',error='서버가 재시작되어 이전 처리의 완료를 확인할 수 없습니다. 자동 재시도하지 않았습니다.',reply=ERROR_MESSAGE,completed_at=now())
  save_record(r,True)

def history_context(records,*,session_id):
 """User statements are source data; assistant history is classification metadata, never business results."""
 selected=[];chars=0
 for r in reversed(records):
  if r.get('session_id')!=session_id:raise ContractError('source_error','이전 대화가 현재 세션에 속하지 않습니다.')
  if r['status']=='pending':continue
  part=[{'role':'user','content':json.dumps({'turn_id':r['request_id'],'text':r['question'],'source_type':'user_statement'},ensure_ascii=False)}]
  if r['status']=='ok':
   prior_ids=[x['intent_id'] for x in (r.get('analysis') or {}).get('requests',[]) if x.get('intent_id') in config.INTENT_IDS]
   part.append({'role':'assistant','content':json.dumps({'message_kind':'intent_explanation_only','unverified_interpretation':{'prior_intent_ids':prior_ids},'business_actions_performed':False,'verified_business_results_available':False,'concrete_proposals_available':False},ensure_ascii=False)})
  size=sum(len(m['content']) for m in part)
  if len(selected)>=spec.CONTEXT_TURNS or chars+size>spec.CONTEXT_CHARS:break
  selected.append((r,part));chars+=size
 selected.reverse();ids=[r['request_id'] for r,_ in selected]
 sources={r['request_id']:{'text':r['question'],'session_id':session_id,'role':'user'} for r,_ in selected}
 limits={'max_previous_turns':spec.CONTEXT_TURNS,'max_previous_characters':spec.CONTEXT_CHARS,'included_characters':chars,'included_turn_ids':ids,'omitted_turn_ids':[r['request_id'] for r in records if r['request_id'] not in ids],'history_truncated':len(ids)<len(records),'current_question_included_separately':True,'policy':'이전 사용자 원문과 미확인 분류ID만 전달; 과거 AI 자연어·잘못된 해석은 저장/UI에만 보존; 업무결과·제안은 없음'}
 return [m for _,parts in selected for m in parts],limits,sources

def build_messages(question,records,*,current_turn_id,session_id):
 if not current_turn_id or not session_id:raise ContractError('source_error','실제 사용자 턴/세션 ID가 필요합니다.')
 previous,limits,sources=history_context(records,session_id=session_id)
 if current_turn_id in sources:raise ContractError('source_error','현재 턴이 이전 턴과 중복됩니다.')
 sources[current_turn_id]={'text':question,'session_id':session_id,'role':'user'}
 system=(config.ROOT/'prompts/chatbot_v2.md').read_text()+'\n\n분류 정의:\n'+json.dumps(load_taxonomy(),ensure_ascii=False)+'\n\n맥락 전달 제한(메타데이터):\n'+json.dumps(limits,ensure_ascii=False)
 current={'turn_id':current_turn_id,'text':question,'source_type':'user_statement'}
 return [{'role':'system','content':system},*previous,{'role':'user','content':json.dumps(current,ensure_ascii=False)}],limits,sources

def chat(sid,question):
 """Historical v2 evaluator only. The web API uses chat_flow.submit (v3)."""
 start=time.perf_counter()
 if type(question) is not str or not question.strip() or len(question)>4000:raise ValueError('질문은 1~4000자여야 합니다.')
 r={'session_id':sid,'request_id':uuid.uuid4().hex,'created_at':now(),'question':question,'status':'pending','reply':None,'analysis':None,'llm_call_count':0,'backend':'vllm_qwen','error_kind':None,'error':None,'model_http_seconds':None,'error_stage':None,'validation_checks':{'format':'not_checked','source':'not_checked','meaning':'not_automatically_verified'},**version_info()}
 with db() as conn:
  conn.execute('BEGIN IMMEDIATE')
  if not conn.execute('SELECT 1 FROM sessions WHERE session_id=?',(sid,)).fetchone():raise SessionMissing()
  if conn.execute("SELECT 1 FROM turns WHERE session_id=? AND status='pending'",(sid,)).fetchone():raise SessionBusy()
  prior=[json.loads(x[0]) for x in conn.execute('SELECT record_json FROM turns WHERE session_id=? ORDER BY rowid',(sid,)).fetchall()]
  conn.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',(r['request_id'],sid,r['created_at'],question,'pending',json.dumps(r,ensure_ascii=False)))
 try:
  # Same application-wide model lock and active-evaluation guard as the test route.
  with classifier.LOCK:
   marker=config.ROOT/'data/runtime/evaluation.lock'
   if marker.exists() and not os.getenv('LAM_EVALUATOR_PID'):
    try:pid=int(marker.read_text());os.kill(pid,0)
    except (ValueError,ProcessLookupError):pass
    else:raise llm_client.ModelError('evaluation_running','평가 중에는 챗봇 모델 호출을 차단합니다.')
   target=config.targets()['vllm_qwen'];availability=llm_client.probe(target);r['availability']=availability
   if not availability['can_run']:raise llm_client.ModelError('unavailable',availability.get('reason') or 'vLLM 연결을 확인하세요.')
   msgs,limits,sources=build_messages(question,prior,current_turn_id=r['request_id'],session_id=sid);path,body=llm_client.payload(target,msgs,schema=spec.SCHEMA,max_tokens=spec.MAX_TOKENS)
   r.update(model=target.model,think_requested=False,settings_requested={'temperature':0,'seed':20261012,'output_limit':spec.MAX_TOKENS},context_limits=limits,request=config.clean(body),endpoint=target.url+path,llm_call_count=1)
   save_record(r);http_start=time.perf_counter()
   try:r.update(llm_client.infer(target,msgs,schema=spec.SCHEMA,max_tokens=spec.MAX_TOKENS))
   except Exception:r['model_http_seconds']=time.perf_counter()-http_start;raise
   analysis=validate(r['content'],r['finish_reason'],r['tool_calls'],sources,session_id=sid)
   r['validation_checks'].update(format='passed',source='passed')
   byid={x['id']:x for x in load_taxonomy()}
   r.update(status='ok',analysis=analysis,reply=analysis['reply'],intent_labels=[{'id':x['intent_id'],'name':byid[x['intent_id']]['name']} for x in analysis['requests']])
 except ContractError as exc:
  r.update(status='chat_error',error_kind=exc.kind,error_stage='source' if exc.kind=='source_error' else 'format',error=str(exc),reply=ERROR_MESSAGE)
  r['validation_checks'].update(format='passed' if exc.kind=='source_error' else 'failed',source='failed' if exc.kind=='source_error' else 'not_checked')
 except llm_client.ModelError as exc:
  r.update(status='chat_error',error_kind=exc.kind,error_stage='format' if exc.kind=='truncated' else 'api',error=str(exc),reply=ERROR_MESSAGE)
  if exc.kind=='truncated':r['validation_checks']['format']='failed'
 except (ValueError,KeyError,TypeError) as exc:r.update(status='chat_error',error_kind='format_error',error_stage='format',error='챗봇 출력 검증 실패: '+str(exc),reply=ERROR_MESSAGE)
 except Exception:r.update(status='chat_error',error_kind='internal_error',error='의도 분석 중 내부 오류가 발생했습니다.',reply=ERROR_MESSAGE)
 r.update(elapsed_seconds=time.perf_counter()-start,completed_at=now())
 return save_record(r,True)
