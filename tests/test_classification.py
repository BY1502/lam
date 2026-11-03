import json
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from backend import classifier as c,llm_client as llm,config
from backend.main import app

def test_definitions_verbatim():
 import re
 source=(config.ROOT/'taxonomy/second_review_source.txt').read_text().split('[2. 2차정리의 의도 목록과 정의]\n')[1].split('\n- 위 정의를')[0]
 rows=[{'id':m[2],'name':m[3],'description':m[4].strip('\n')} for m in re.finditer(r'(?m)^(\d+)\. ([a-z_.]+) — (.+)\n([\s\S]*?)(?=\n\d+\. |\Z)',source)]
 assert len(rows)==15 and rows==c.load_taxonomy()

def test_only_definitions_and_question():
 msg=c.messages('테스트 원문',c.load_taxonomy());assert [x['role'] for x in msg]==['system','user'] and msg[1]['content']=='테스트 원문'
 assert '필요한 도구' not in msg[0]['content'] and '1~66' not in msg[0]['content']
 for path in (config.ROOT/'evaluation/datasets/second_review').glob('*.json'):
  for r in json.loads(path.read_text()):assert r['question'] not in msg[0]['content']

@pytest.mark.parametrize('value',['','```json\n{}\n```','prefix {"intents":["clarify"]}','{"intents":["clarify"]} tail','{"intents":[]}','{"intents":[1]}','{"intents":[true]}','{"intents":["foo"]}','{"intents":"clarify"}','{"intents":["clarify","clarify"]}','{"intents":["clarify"],"answer":"x"}','{"intents":["clarify"],"intents":["out_of_scope"]}','{"status":"classified","intent_id":1}','null'])
def test_reject_invalid(value):
 with pytest.raises((ValueError,TypeError,llm.ModelError)):c.validate(value,'stop')

def test_accept_three_and_all_ids_no_artificial_cap():
 assert c.validate(json.dumps({'intents':list(config.INTENT_IDS)}),'stop')['intents']==list(config.INTENT_IDS)

def test_cutoff_and_tools_rejected():
 for content,finish,tools in [('{"intents":["clarify"]}','length',[]),('{"intents":["clarify"]}','stop',[{'name':'fake'}])]:
  with pytest.raises((ValueError,llm.ModelError)):c.validate(content,finish,tools)

def test_wire_schema_no_tools():
 for t in config.targets().values():
  _,p=llm.payload(t,c.messages('test',c.load_taxonomy()));assert 'tools' not in p
  schema=p['response_format']['json_schema']['schema'] if t.engine=='vllm' else p['format']
  assert set(schema['properties'])=={'intents'} and not schema['additionalProperties']
  assert len(schema['properties']['intents']['items']['enum'])==15
  if t.engine=='vllm':assert p['max_tokens']==256 and p['seed']==20261012 and p['temperature']==0 and p['chat_template_kwargs']['enable_thinking'] is False

def fake(t,m):return {'content':'{"intents":["history.query","data.analyze"]}','finish_reason':'stop','tool_calls':[],'model_http_seconds':.01}
def test_one_call_and_server_names(monkeypatch):
 monkeypatch.setenv('LAM_EVALUATOR_PID','test')
 with patch.object(llm,'probe',return_value={'can_run':True}),patch.object(llm,'infer',side_effect=fake) as infer:r=c.classify('vllm_qwen','测试',False)
 assert infer.call_count==r['llm_call_count']==1 and r['intents']==['history.query','data.analyze']
 assert r['intent_definitions']==c.load_taxonomy()[1:2]+c.load_taxonomy()[3:4]

def test_errors_no_retry(monkeypatch):
 monkeypatch.setenv('LAM_EVALUATOR_PID','test')
 for error in [llm.ModelError('timeout','timeout'),llm.ModelError('api_error','error')]:
  with patch.object(llm,'probe',return_value={'can_run':True}),patch.object(llm,'infer',side_effect=error) as infer:r=c.classify('vllm_qwen','x',False)
  assert infer.call_count==1 and r['status']=='classification_error' and r['intents']==[]

def test_bad_output_not_corrected(monkeypatch):
 monkeypatch.setenv('LAM_EVALUATOR_PID','test')
 with patch.object(llm,'probe',return_value={'can_run':True}),patch.object(llm,'infer',return_value={'content':'{"intents":["status.query","status.query"]}','finish_reason':'stop','tool_calls':[]}) as infer:r=c.classify('vllm_qwen','x',False)
 assert infer.call_count==1 and r['status']=='classification_error' and r['error_kind']=='validation_error'

def test_api_validation_and_health():
 client=TestClient(app)
 for body in [{'backend':'unknown','question':'x'},{'backend':'vllm_qwen','question':''},{'backend':'vllm_qwen','question':'   '},{'backend':'vllm_qwen','question':'x','expected_intents':[]}]:assert client.post('/api/classify',json=body).status_code==422
 assert client.get('/api/health').json()['taxonomy']['registered']
 assert len(client.get('/api/intents').json()['items'])==15

def test_evaluation_list_version_isolation():
 client=TestClient(app)
 for r in client.get('/api/evaluations').json():assert not r['id'].startswith('fresh-20261027')

def test_secret_scrub(monkeypatch):
 monkeypatch.setenv('VLLM_API_KEY','secret-value');assert 'secret-value' not in str(config.clean({'authorization':'secret-value','error':'secret-value'}))

def test_v2_scoring_errors_and_set_order_separated():
 from evaluation.run_second_review import correct,metric
 def row(ids,status='classified'):
  return {'case':{'expected_intents':['status.query','history.query']},'record':{'status':status,'intents':ids,'elapsed_seconds':1,'model_http_seconds':.8}}
 rows=[row(['history.query','status.query']),row(['status.query']),row([],'classification_error')]
 assert correct(rows[0]) and not correct(rows[2])
 m=metric(rows);assert m['n']==3 and m['correct']==1 and m['ordered_correct']==0 and m['valid']==2

def test_dataset_coverage_and_frozen_ambiguity():
 root=config.ROOT/'evaluation/datasets/second_review'
 rows=json.loads((root/'single.json').read_text());assert len(rows)==75
 assert all(sum(r['expected_intents']==[i] for r in rows)==5 for i in config.INTENT_IDS)
 boundary=json.loads((root/'boundary.json').read_text());from collections import Counter
 counts=Counter(r['boundary'] for r in boundary);assert len(counts)==13 and min(counts.values())>=4
 assert all(not r['scored'] and not r['expected_intents'] for r in json.loads((root/'review.json').read_text()))
