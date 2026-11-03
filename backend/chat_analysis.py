"""Strict analysis/source validation and bounded, versioned user-context slots. No work execution."""
import copy,json,re
from . import classifier,chat_contract,chat_schema,chat_flow_schema as spec,config
from .chat_contract import ContractError

def validate(content,finish_reason,tool_calls,sources,*,session_id,current_turn_id,prior_memory):
 try:a=json.loads(content,object_pairs_hook=classifier.unique)
 except (TypeError,ValueError):raise ContractError('format_error','분석 JSON 형식 또는 중복 필드 오류')
 if type(a) is not dict or set(a)!={'requests','memory'}:raise ContractError('format_error','분석은 requests/memory 필드만 허용합니다.')
 if not isinstance(a['requests'],list) or not a['requests']:raise ContractError('format_error','빈 분석 요청')
 required=set(spec.REQUEST['required']);base=[]
 def check_evidence(ev):
  req={'interpreted_request':'출처 연결 검사','intent_id':'clarify','reason':'기계적 출처 검사','target_expressions':[],'time_expression':None,'conditions':[],'evidence':ev}
  chat_contract.validate(json.dumps({'requests':[req],'reply':'내부 검증 표식'},ensure_ascii=False),finish_reason,tool_calls,sources,session_id=session_id)
 for req in a['requests']:
  if type(req) is not dict or set(req)!=required:raise ContractError('format_error','요청 분석 필드 누락/추가')
  if type(req['missing_information']) is not list or any(type(x) is not str or not x.strip() for x in req['missing_information']):raise ContractError('format_error','부족 정보 배열 오류')
  q=req['clarification_question']
  if req['intent_id']=='clarify':
   if type(q) is not str or not q.strip():raise ContractError('format_error','clarify에는 확인 질문이 필요합니다.')
  elif q is not None:raise ContractError('format_error','clarify 외에는 확인 질문이 null이어야 합니다.')
  base.append({k:v for k,v in req.items() if k in chat_schema.REQUEST['required']})
 chat_contract.validate(json.dumps({'requests':base,'reply':'내부 검증 표식'},ensure_ascii=False),finish_reason,tool_calls,sources,session_id=session_id)
 m=a['memory']
 if type(m) is not dict or set(m)!={'relation','updates','remove'} or m['relation'] not in ['new_task','continue','correct','withdraw']:raise ContractError('format_error','메모리 관계/필드 오류')
 old={f['slot']:copy.deepcopy(f) for f in prior_memory}
 if type(m['updates']) is not list or type(m['remove']) is not list:raise ContractError('format_error','메모리 갱신 배열 오류')
 seen=set()
 for items,fields in [(m['updates'],{'slot','value','evidence'}),(m['remove'],{'slot','evidence'})]:
  for x in items:
   if type(x) is not dict or set(x)!=fields or type(x['slot']) is not str or not re.fullmatch('[a-z][a-z0-9_.]{0,63}',x['slot']):raise ContractError('format_error','메모리 슬롯 형식 오류')
   if x['slot'] in seen:raise ContractError('format_error','메모리 슬롯 중복')
   seen.add(x['slot']);check_evidence(x['evidence'])
   if not any(e['turn_id']==current_turn_id for e in x['evidence']):raise ContractError('source_error','메모리 변경에는 현재 사용자 발언 근거가 필요합니다.')
   if 'value' in x and (type(x['value']) is not str or not x['value'].strip()):raise ContractError('format_error','빈 슬롯 해석')
 if any(x['slot'] not in old for x in m['remove']):raise ContractError('source_error','철회할 기존 활성 슬롯을 확인할 수 없습니다.')
 if m['relation']=='new_task':
  # Removing validated old slots is cleanup, not inheritance. A new task resets
  # the whole snapshot regardless of whether the model lists that cleanup.
  for x in [*a['requests'],*m['updates']]:
   if any(e['turn_id']!=current_turn_id for e in x['evidence']):raise ContractError('source_error','새 작업에 이전 작업의 근거를 혼입할 수 없습니다.')
 if old and m['relation']=='correct' and not (any(x['slot'] in old for x in m['updates']) or m['remove']):raise ContractError('format_error','정정은 기존 슬롯을 지정하여 대체해야 합니다.')
 active={} if m['relation']=='new_task' else old
 for x in m['remove']:active.pop(x['slot'],None)
 for x in m['updates']:active[x['slot']]={**copy.deepcopy(x),'revision_request_id':current_turn_id,'source_type':'model_interpretation_of_user_statement','business_verified':False}
 return a,list(active.values())

def failure_details(record):
 """Terminal audit only; never treat an unvalidated parse as accepted analysis."""
 raw=(record.get('analysis_stage') or {}).get('content')
 if raw is None:return None
 try:
  parsed=json.loads(raw,object_pairs_hook=classifier.unique);parse_status='parsed';parse_error=None
 except (ValueError,TypeError) as e:
  parsed=None;parse_status='invalid_json';parse_error=str(e)
 return {'request_id':record['request_id'],'model_output':raw,'parse_status':parse_status,
         'parsed_analysis':parsed,'parse_error':parse_error,'validated':False,
         'validation_failure':{'kind':record.get('error_kind'),'stage':record.get('error_stage'),'message':record.get('error')},
         'notice':'미검증 분석 원문·파싱 결과입니다. 정상 분류/활성 메모리로 사용하지 않습니다.'}

def bounded_memory(records,sources):
 last=next((r for r in reversed(records) if r.get('analysis_validated') and 'memory_snapshot' in r),None)
 memory=last['memory_snapshot'] if last else [];kept=[];omitted=[];chars=0
 for f in reversed(memory):
  text=json.dumps(f,ensure_ascii=False)
  if chars+len(text)>4000 or not all(e['turn_id'] in sources for e in f.get('evidence',[])):omitted.append(f['slot']);continue
  kept.append(f);chars+=len(text)
 return list(reversed(kept)),{'memory_version':spec.MEMORY_VERSION,'max_characters':4000,'included_characters':chars,'omitted_slots':omitted,'selection':'최신 검증 분석의 활성 슬롯, 전달된 사용자 출처가 전부 있고 총4000자 안인 항목만. 생략은 복원하지 않음.'}
