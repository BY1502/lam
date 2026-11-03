"""The only engine-specific code. No retries, downloads, or model switches."""
import json,time,urllib.request,urllib.error,os,subprocess
from urllib.parse import urlsplit
from . import config

class ModelError(Exception):
 def __init__(self,kind,message):self.kind=kind;super().__init__(message)

def http(target,path,payload=None,timeout=5):
 headers={'Content-Type':'application/json'}
 if target.key:headers['Authorization']='Bearer '+target.key
 req=urllib.request.Request(target.url+path,data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,headers=headers)
 try:
  with urllib.request.urlopen(req,timeout=timeout) as f:return json.load(f)
 except urllib.error.HTTPError as e:raise ModelError('api_error',f'모델 API HTTP {e.code}') from None
 except (TimeoutError, __import__('socket').timeout):raise ModelError('timeout','모델 API 응답 시간 초과') from None
 except urllib.error.URLError:raise ModelError('connection_error','모델 API 연결 실패') from None
 except (ValueError,KeyError):raise ModelError('api_error','모델 API 응답 형식 오류') from None

def local_ollama_confirmed_stopped(t):
 """Read-only proof for this server's bound Ollama; no service mutation in UI."""
 parsed=urlsplit(t.url)
 if parsed.hostname not in ('127.0.0.1','localhost') or (parsed.port or 80)!=11434:return False
 try:
  raw=subprocess.check_output(['docker','inspect','ollama','--format','{{json .State.Running}} {{json .HostConfig.PortBindings}}'],text=True,stderr=subprocess.DEVNULL,timeout=3)
  running,ports=raw.split(' ',1);bindings=json.loads(ports).get('11434/tcp',[])
  return running=='false' and any(x.get('HostPort')=='11434' and x.get('HostIp') in ('127.0.0.1','') for x in bindings)
 except Exception:return False

def probe(t):
 result={'id':t.id,'engine':t.engine,'url':t.url,'model':t.model,'thinking_requested':t.think,'available':False,'loaded':None,'can_run':False,'reason':None,'checked_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
 try:
  if t.engine=='vllm':
   v=http(t,'/models');models=v.get('data',[])
   found=next((m for m in models if m['id']==t.model),None)
   if found is None:raise ModelError('unavailable','API 모델 목록에 지정 모델이 없습니다.')
   result.update(available=True,loaded=True,capabilities={'max_model_len':found.get('max_model_len')},option_support='enable_thinking=false는 실제 준비 요청 응답으로 확인; GET만으로 적용 여부 확정 안 함')
   other=config.targets()['ollama_qwen']
   if local_ollama_confirmed_stopped(other):result['can_run']=True
   else:result['reason']='단일 모델 메모리 정책: vLLM 실행 중에는 Ollama 컨테이너가 중지되어야 합니다.'
  else:
   models=http(t,'/api/tags')['models'];found=next((m for m in models if m['name']==t.model),None)
   if not found:raise ModelError('unavailable','API 모델 목록에 지정 모델이 없습니다.')
   show=http(t,'/api/show',{'model':t.model});values=show.get('thinking',{}).get('values',[])
   if t.think not in values:raise ModelError('unsupported_option','요청 think 값이 /api/show 지원값에 없습니다.')
   loaded=http(t,'/api/ps').get('models',[])
   result.update(available=True,loaded=any(m.get('name')==t.model for m in loaded),capabilities={'thinking':show.get('thinking'),'details':found.get('details'),'digest':found.get('digest'),'template_sha256':__import__('hashlib').sha256(show.get('template','').encode()).hexdigest()},option_support='think는 /api/show 지원값 확인; 실제 적용은 반환 응답으로 별도 확인')
   other=config.targets()['vllm_qwen']
   try:
    running=subprocess.check_output(['docker','inspect','vllm-qwen','--format','{{.State.Running}}'],text=True,stderr=subprocess.DEVNULL,timeout=3).strip()
    vllm_live=running!='false'
   except Exception:vllm_live=True
   foreign=[m for m in loaded if m.get('name')!=t.model]
   if vllm_live:result['reason']='vLLM이 메모리를 점유 중입니다. 평가 전용 절차에서 전환해야 합니다.'
   elif foreign:result['reason']='다른 Ollama 모델이 적재되어 있어 동시 실행을 차단했습니다.'
   else:result['can_run']=True
 except ModelError as e:result['reason']=str(e)
 except Exception:result['reason']='설정 또는 API 확인 실패'
 return config.clean(result)

def payload(t,messages,*,schema=None,max_tokens=256):
 schema=config.SCHEMA if schema is None else schema
 if t.engine=='vllm':return '/chat/completions',{'model':t.model,'messages':messages,'stream':False,'temperature':0,'seed':20261012,'max_tokens':max_tokens,'chat_template_kwargs':{'enable_thinking':False},'response_format':{'type':'json_schema','json_schema':{'name':'intent','strict':True,'schema':schema}}}
 return '/api/chat',{'model':t.model,'messages':messages,'stream':False,'think':t.think,'keep_alive':'10m','options':{'temperature':0,'seed':20261012,'num_predict':max_tokens,'num_ctx':16384},'format':schema}

def infer(t,messages,*,schema=None,max_tokens=256):
 path,body=payload(t,messages,schema=schema,max_tokens=max_tokens);started=time.perf_counter()
 # Exactly one POST. Headers containing credentials are intentionally not recorded.
 raw=http(t,path,body,timeout=float(os.getenv('LAM_TIMEOUT','180')));elapsed=time.perf_counter()-started
 if t.engine=='vllm':
  choice=raw['choices'][0];message=choice['message'];usage=raw.get('usage') or {};reason=choice.get('finish_reason')
  metrics={'input_tokens':usage.get('prompt_tokens'),'output_tokens':usage.get('completion_tokens'),'reasoning_tokens':(usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),'load_duration_ns':None,'prompt_eval_duration_ns':None,'eval_duration_ns':None}
 else:
  message=raw.get('message',{});reason=raw.get('done_reason');metrics={'input_tokens':raw.get('prompt_eval_count'),'output_tokens':raw.get('eval_count'),'reasoning_tokens':None,'load_duration_ns':raw.get('load_duration'),'prompt_eval_duration_ns':raw.get('prompt_eval_duration'),'eval_duration_ns':raw.get('eval_duration')}
 return {'request':config.clean(body),'endpoint':t.url+path,'raw_response':config.clean(raw),'content':message.get('content') or '', 'reasoning':message.get('thinking') or message.get('reasoning') or message.get('reasoning_content'),'finish_reason':reason,'tool_calls':message.get('tool_calls') or [],'model_http_seconds':elapsed,**metrics}


def response_payload(t,messages,*,max_tokens):
 if t.engine!='vllm':raise ModelError('unsupported_backend','챗봇 응답 스트리밍은 현재 vLLM만 사용합니다.')
 path,body=payload(t,messages,max_tokens=max_tokens)
 body.pop('response_format',None);body.update(stream=True,stream_options={'include_usage':True})
 return path,body

def stream_response(t,messages,*,max_tokens,cancel):
 """One genuine vLLM SSE POST. Never retries, splits completed text, or forwards reasoning."""
 path,body=response_payload(t,messages,max_tokens=max_tokens);headers={'Content-Type':'application/json'}
 if t.key:headers['Authorization']='Bearer '+t.key
 req=urllib.request.Request(t.url+path,data=json.dumps(body,ensure_ascii=False).encode(),headers=headers)
 started=time.perf_counter();finish=None;usage={};done=False
 try:
  with urllib.request.urlopen(req,timeout=float(os.getenv('LAM_TIMEOUT','180'))) as f:
   for line in f:
    if cancel.is_set():raise ModelError('cancelled','응답 스트리밍이 중단되었습니다.')
    if not line.startswith(b'data:'):continue
    data=line[5:].strip()
    if data==b'[DONE]':done=True;break
    try:item=json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError,ValueError):raise ModelError('stream_format_error','모델 스트림의 JSON/UTF-8 형식 오류') from None
    if item.get('error'):raise ModelError('api_error','모델 스트림 오류')
    if item.get('usage'):usage=item['usage']
    for choice in item.get('choices',[]):
     delta=choice.get('delta') or {}
     if delta.get('tool_calls'):raise ModelError('unexpected_tool_calls','응답 단계의 도구 호출은 허용되지 않습니다.')
     # reasoning/reasoning_content are never emitted to the application stream.
     if delta.get('content'):
      if type(delta['content']) is not str:raise ModelError('stream_format_error','본문 조각 자료형 오류')
      yield {'type':'delta','text':delta['content'],'http_elapsed_seconds':time.perf_counter()-started}
     if choice.get('finish_reason'):finish=choice['finish_reason']
  if cancel.is_set():raise ModelError('cancelled','응답이 중단되었습니다.')
  if not done or finish is None:raise ModelError('stream_disconnected','모델 스트림이 완료 신호 전에 종료됐습니다.')
  yield {'type':'end','finish_reason':finish,'usage':usage,'model_http_seconds':time.perf_counter()-started}
 except urllib.error.HTTPError as e:raise ModelError('api_error',f'모델 API HTTP {e.code}') from None
 except (TimeoutError,__import__('socket').timeout):raise ModelError('timeout','모델 응답 스트림 시간 초과') from None
 except urllib.error.URLError:raise ModelError('connection_error','모델 응답 스트림 연결 실패') from None
