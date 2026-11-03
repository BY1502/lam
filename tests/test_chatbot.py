import json,threading
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from backend import chatbot as c,chat_schema as spec,classifier,llm_client,config
from backend.chat_contract import ContractError
from backend.main import app

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
 monkeypatch.setattr(c,'DB_PATH',tmp_path/'sessions.sqlite3');monkeypatch.setattr(c,'LOG_PATH',tmp_path/'requests.jsonl');monkeypatch.setenv('LAM_EVALUATOR_PID','test')

def result(q='지금 온도는?',iid='status.query',turn='t1',**overrides):
 req={'interpreted_request':'요청 목적 해석','intent_id':iid,'reason':'요청 행동에 따른 분류','target_expressions':[],'time_expression':None,'conditions':[],'evidence':[{'turn_id':turn,'quote':q}],**overrides}
 return {'requests':[req],'reply':'요청 목적을 이해했습니다. 실제 업무 처리는 하지 않았습니다.'}
def sources(q='지금 온도는?'):return {'t1':{'text':q,'session_id':'s1','role':'user'}}
def validate(a,src=None,finish='stop',tools=None):return c.validate(json.dumps(a,ensure_ascii=False),finish,tools or [],src or sources(),session_id='s1')
def fake(t,msgs,**kwargs):
 user=json.loads(msgs[-1]['content']);a=result(user['text'],turn=user['turn_id']);return {'content':json.dumps(a,ensure_ascii=False),'finish_reason':'stop','tool_calls':[],'model_http_seconds':.01,'raw_response':{}}
def run(sid,q='지금 온도는?'):
 with patch.object(llm_client,'probe',return_value={'can_run':True}),patch.object(llm_client,'infer',side_effect=fake):return c.chat(sid,q)

def test_normalization_is_interpretation_not_quote():
 q='포장 인원 여섯 명을 세 작업에 배치하는 시간표를 만들어줘.';a=result(q,iid='work.plan',target_expressions=['포장 인원'],conditions=['인원 6명','작업 3개'])
 assert validate(a,sources(q))==a
 a['requests'][0]['evidence'][0]['quote']='포장 인원 6명을 3개 작업에 배치'
 with pytest.raises(ContractError,match='연속'):validate(a,sources(q))

@pytest.mark.parametrize('change,kind',[
 (lambda a:a.update(answer='x'),'format_error'),(lambda a:a.update(requests=[]),'format_error'),(lambda a:a.update(reply=''),'format_error'),(lambda a:a['requests'][0].update(intent_id='bad'),'format_error'),(lambda a:a['requests'][0].update(extra=1),'format_error'),(lambda a:a['requests'][0].pop('conditions'),'format_error'),(lambda a:a['requests'][0].update(conditions=[6]),'format_error'),(lambda a:a['requests'][0].update(evidence=[]),'format_error'),(lambda a:a['requests'][0]['evidence'][0].update(turn_id='unknown'),'source_error'),(lambda a:a['requests'][0]['evidence'][0].update(quote='없는 인용'),'source_error')])
def test_negative_schema_and_source(change,kind):
 a=result();change(a)
 with pytest.raises(ContractError) as exc:validate(a)
 assert exc.value.kind==kind

def test_other_turn_session_and_assistant_source_rejected():
 for src in [{'t1':{'text':'다른 원문','session_id':'s1','role':'user'}},{'t1':{'text':'지금 온도는?','session_id':'other','role':'user'}},{'t1':{'text':'지금 온도는?','session_id':'s1','role':'assistant'}}]:
  with pytest.raises(ContractError) as exc:validate(result(),src)
  assert exc.value.kind=='source_error'

def test_disconnected_fragments_are_separate_quotes():
 q='혼합시간 8분과 12분이라는 두 조건을 비교해줘.'
 a=result(q,iid='whatif.simulate',conditions=['혼합시간 8분','혼합시간 12분']);a['requests'][0]['evidence']=[{'turn_id':'t1','quote':'혼합시간 8분'},{'turn_id':'t1','quote':'12분이라는 두 조건'}]
 assert validate(a,sources(q))==a
 a['requests'][0]['evidence']=[{'turn_id':'t1','quote':'혼합시간 12분'}]
 with pytest.raises(ContractError):validate(a,sources(q))

def test_no_whitespace_or_number_repair_of_quote():
 q='인원 여섯 명'
 for bad in ['인원여섯명','인원 6명','인원 아홉 명']:
  a=result(q);a['requests'][0]['evidence'][0]['quote']=bad
  with pytest.raises(ContractError):validate(a,sources(q))


def test_semantic_negatives_are_not_claimed_mechanically_verified():
 q='인원 여섯 명을 배치해줘.'
 for value in [result(q,conditions=['인원 999명']),result(q,time_expression='2099년 1월 1일')]:
  assert validate(value,sources(q))==value # Sources exist; invented interpretation requires semantic review, not substring tricks.
 a=result(q);a['reply']='조회가 완료됐고 구체적인 제안을 적용했습니다.';assert validate(a,sources(q))==a
 sid=c.create_session()['session_id'];r=run(sid)
 assert r['validation_checks']=={'format':'passed','source':'passed','meaning':'not_automatically_verified'}


def test_all_same_session_user_sources_and_current_id():
 sid=c.create_session()['session_id'];a=run(sid,'1호 라인 오븐의 온도를 알려줘.');current='current-real-test-id'
 msgs,limits,src=c.build_messages('어제 기록이야',c.session_history(sid),current_turn_id=current,session_id=sid)
 assert src[current]['text']=='어제 기록이야' and src[a['request_id']]['session_id']==sid
 assert json.loads(msgs[-1]['content'])=={'turn_id':current,'text':'어제 기록이야','source_type':'user_statement'}
 assert 'business_actions_performed' in msgs[2]['content'] and json.loads(msgs[2]['content'])['verified_business_results_available'] is False
 other=c.create_session()['session_id'];fresh,_,src=c.build_messages('어제 기록이야',[],current_turn_id='fresh',session_id=other);assert len(fresh)==2 and len(src)==1
 with pytest.raises(ContractError):c.build_messages('x',c.session_history(sid),current_turn_id='x',session_id=other)


def test_old_model_text_is_stored_but_not_used_as_user_evidence():
 sid=c.create_session()['session_id'];old={'session_id':sid,'request_id':'old','question':'물 온도를 알려줘','status':'ok','analysis':{'requests':[{'intent_id':'knowledge.lookup'}],'reply':'문서에记载된 값이 확인됐고 제안을 적용했습니다.'}}
 before=json.dumps(old,ensure_ascii=False);msgs,_,src=c.build_messages('기록을 보여줘',[old],current_turn_id='current',session_id=sid)
 assert before==json.dumps(old,ensure_ascii=False) and '记载된' not in json.dumps(msgs,ensure_ascii=False)
 assert src['old']['text']==old['question']
 a=result('기록을 보여줘',turn='current');a['requests'][0]['evidence']=[{'turn_id':'old','quote':'제안을 적용했습니다.'}]
 with pytest.raises(ContractError):c.validate(json.dumps(a),'stop',[],src,session_id=sid)


def test_one_inference_and_original_classification_contract():
 sid=c.create_session()['session_id']
 with patch.object(llm_client,'probe',return_value={'can_run':True}),patch.object(llm_client,'infer',side_effect=fake) as infer:r=c.chat(sid,'현재 온도를 알려줘')
 assert infer.call_count==r['llm_call_count']==1 and r['status']=='ok'
 assert infer.call_args.kwargs=={'schema':spec.SCHEMA,'max_tokens':4096}
 assert 'tools' not in r['request'] and json.loads(r['request']['messages'][-1]['content'])['text']==r['question']
 assert r['request_id'] in {x['turn_id'] for x in r['analysis']['requests'][0]['evidence']}
 assert r['chat_schema_version']==spec.SCHEMA_VERSION
 _,normal=llm_client.payload(config.targets()['vllm_qwen'],[]);assert normal['max_tokens']==256 and normal['response_format']['json_schema']['schema']==config.SCHEMA


def test_errors_separated_no_retry():
 sid=c.create_session()['session_id']
 for error in [llm_client.ModelError('timeout','timeout'),llm_client.ModelError('api_error','api')]:
  with patch.object(llm_client,'probe',return_value={'can_run':True}),patch.object(llm_client,'infer',side_effect=error) as f:r=c.chat(sid,'x')
  assert f.call_count==1 and r['error_stage']=='api' and r['analysis'] is None
 for raw,kind,stage in [('{"invalid":1}','format_error','format'),(json.dumps(result('wrong',turn='unknown')),'source_error','source')]:
  with patch.object(llm_client,'probe',return_value={'can_run':True}),patch.object(llm_client,'infer',return_value={'content':raw,'finish_reason':'stop','tool_calls':[]}) as f:r=c.chat(sid,'x')
  assert f.call_count==1 and r['error_kind']==kind and r['error_stage']==stage and r['analysis'] is None
 assert run(sid)['status']=='ok'


def test_truncation_duplicate_json_and_tools_not_fixed():
 for raw,finish,tools in [(json.dumps(result()),'length',[]),('{"requests":[],"requests":[],"reply":"x"}','stop',[]),(json.dumps(result()),'stop',[{'name':'x'}]),('```json\n{}\n```','stop',[])]:
  with pytest.raises((ContractError,llm_client.ModelError)):c.validate(raw,finish,tools,sources(),session_id='s1')


def test_duplicate_ids_allowed_but_quote_duplicate_rejected():
 a=result();a['requests']*=4;assert len(validate(a)['requests'])==4
 a=result();a['requests'][0]['evidence']*=2
 with pytest.raises(ContractError):validate(a)


def test_context_limit_and_error_output_exclusion(monkeypatch):
 sid=c.create_session()['session_id'];a=run(sid,'오래된 질문');b=run(sid,'최근 정정');monkeypatch.setattr(spec,'CONTEXT_TURNS',1)
 msgs,lim,src=c.build_messages('새 질문',c.session_history(sid),current_turn_id='cur',session_id=sid)
 assert set(src)=={b['request_id'],'cur'} and lim['history_truncated'] and a['request_id'] in lim['omitted_turn_ids']
 assert '오래된 질문' not in json.dumps(msgs,ensure_ascii=False)


def test_duplicate_submission_and_api_boundaries():
 sid=c.create_session()['session_id'];entered=threading.Event();release=threading.Event()
 def blocked(*args,**kw):entered.set();release.wait(5);return fake(*args,**kw)
 with patch.object(llm_client,'probe',return_value={'can_run':True}),patch.object(llm_client,'infer',side_effect=blocked) as f:
  t=threading.Thread(target=lambda:c.chat(sid,'지금 온도는?'));t.start();assert entered.wait(3)
  try:
   with pytest.raises(c.SessionBusy):c.chat(sid,'중복')
  finally:release.set();t.join(5)
 assert f.call_count==1
 client=TestClient(app)
 for body in [{'question':' '},{'question':'x','evidence':[]},{'question':'x','session_id':'other'}]:assert client.post(f'/api/chat/sessions/{sid}/messages',json=body).status_code==422
 assert client.get('/api/chat/sessions/notfound').status_code==404


def test_taxonomy_and_language_rules_unchanged():
 assert len(c.load_taxonomy())==15
 assert [(x['id'],x['name']) for x in c.load_taxonomy()]==[(x['id'],x['name']) for x in classifier.load_taxonomy()]
 msgs,_,_=c.build_messages('현재 질문',[],current_turn_id='current',session_id='s1');assert '자연스러운 한국어' in msgs[0]['content'] and '1~66' not in msgs[0]['content']
