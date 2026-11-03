"""Mechanical source contract only. Semantic truth is explicitly unverified."""
import json
from . import classifier,config,chat_schema as spec,llm_client

class ContractError(ValueError):
 def __init__(self,kind,message):self.kind=kind;super().__init__(message)

def validate(content,finish_reason,tool_calls,sources,*,session_id):
 if finish_reason in ('length','max_tokens'):raise llm_client.ModelError('truncated','챗봇 출력 한도에 도달했습니다. 재호출하지 않습니다.')
 def fail(message):raise ContractError('format_error',message)
 def source_fail(message):raise ContractError('source_error',message)
 if tool_calls:fail('도구 호출은 허용하지 않습니다.')
 try:value=json.loads(content,object_pairs_hook=classifier.unique)
 except (ValueError,TypeError):fail('유효한 단일 JSON이 아니거나 중복 필드가 있습니다.')
 string=lambda v:type(v) is str and bool(v.strip())
 if type(value) is not dict or set(value)!={'requests','reply'}:fail('requests/reply 필드만 필요합니다.')
 if not string(value['reply']) or type(value['requests']) is not list or not value['requests']:fail('빈 설명/요청 또는 자료형 오류')
 for req in value['requests']:
  if type(req) is not dict or set(req)!=set(spec.REQUEST['required']):fail('요청 필드 누락/추가')
  if type(req['intent_id']) is not str or req['intent_id'] not in config.INTENT_IDS:fail('허용되지 않은 의도 ID')
  if not string(req['interpreted_request']) or not string(req['reason']):fail('빈 목적/분류 이유')
  for key in ['target_expressions','conditions']:
   if type(req[key]) is not list or any(not string(x) for x in req[key]):fail('해석 배열의 자료형 오류')
  if req['time_expression'] is not None and not string(req['time_expression']):fail('해석 기간 자료형 오류')
  if type(req['evidence']) is not list or not req['evidence']:fail('각 해석 요청에 원문 근거가 하나 이상 필요합니다.')
  seen=set()
  for e in req['evidence']:
   if type(e) is not dict or set(e)!={'turn_id','quote'} or not string(e['turn_id']) or not string(e['quote']):fail('근거 필드/자료형 오류')
   source=sources.get(e['turn_id'])
   if not source or source.get('session_id')!=session_id or source.get('role')!='user':source_fail('해당 세션·전달 범위의 실제 사용자 턴이 아닙니다.')
   if e['quote'] not in source['text']:source_fail('인용이 해당 사용자 발언에 연속된 구절로 존재하지 않습니다.')
   pair=(e['turn_id'],e['quote'])
   if pair in seen:source_fail('같은 요청에서 동일 인용이 중복되었습니다.')
   seen.add(pair)
 # No substring assertion on normalized interpretation; semantic correctness is not inferred.
 return value
