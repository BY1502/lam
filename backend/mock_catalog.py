"""Catalog/mapping metadata only; deliberately no callable adapters or execution interface."""
import json,hashlib
from . import config
PATH=config.ROOT/'settings/mock_tools.json'
def load():
 value=json.loads(PATH.read_text());byid={x['id']:x for x in value['tools']}
 if value['mode']!='mock' or value['execution_enabled'] is not False or set(value['intent_tools'])!=set(config.INTENT_IDS):raise ValueError('모의 카탈로그 계약 오류')
 for x in value['tools']:
  if x['mode']!='mock' or any(x[k] is not False for k in ['implemented','execution_enabled','write_enabled','approved']):raise ValueError('실제 실행/쓰기 구성은 허용하지 않습니다.')
 for ids in value['intent_tools'].values():
  if any(i not in byid for i in ids):raise ValueError('등록되지 않은 모의 도구 ID')
 return value

def connections(requests):
 c=load();byid={x['id']:x for x in c['tools']}
 return {'mode':'mock','version':c['version'],'sha256':hashlib.sha256(PATH.read_bytes()).hexdigest(),'execution_enabled':False,'actual_tool_calls':0,'assumptions':c['assumptions'],'requests':[{'request_index':i,'intent_id':r['intent_id'],'status':'out_of_scope' if r['intent_id']=='out_of_scope' else 'clarify' if r['intent_id']=='clarify' else 'valid','candidates':[byid[t] for t in c['intent_tools'][r['intent_id']]],'selection_method':'검증된 의도 ID에 대한 설정상 연결 후보. LLM tool_calls/실행 아님.'} for i,r in enumerate(requests)]}
