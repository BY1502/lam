import copy,io,json,threading,time,uuid
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from backend import chatbot as store,chat_flow as flow,chat_analysis,chat_flow_schema as spec,llm_client,config,mock_catalog
from backend.chat_contract import ContractError
from backend.main import app

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
 monkeypatch.setattr(store,'DB_PATH',tmp_path/'sessions.sqlite3');monkeypatch.setattr(store,'LOG_PATH',tmp_path/'audit.jsonl');monkeypatch.setenv('LAM_EVALUATOR_PID','unit-test')
 yield
 # Every test waits for its own jobs; no thread is allowed to outlive the isolated database.
 for j in list(flow.JOBS.values()):
  if not j.ended:
   j.cancel.set()
   deadline=time.monotonic()+3
   while not j.ended and time.monotonic()<deadline:time.sleep(.01)

def analysis(q,turn,iid='status.query',memory=None,**fields):
 request={'interpreted_request':'사용자가 원하는 처리 목적','intent_id':iid,'reason':'업무 목적을 분류','target_expressions':[],'time_expression':None,'conditions':[],'evidence':[{'turn_id':turn,'quote':q}],'missing_information':['요청 행동'] if iid=='clarify' else [],'clarification_question':'어떤 처리를 원하시나요?' if iid=='clarify' else None,**fields}
 return {'requests':[request],'memory':memory or {'relation':'new_task','updates':[],'remove':[]}}

def fake_infer(t,msgs,**kw):
 u=json.loads(msgs[-1]['content']);a=analysis(u['text'],u['turn_id'])
 return {'content':json.dumps(a,ensure_ascii=False),'finish_reason':'stop','tool_calls':[],'model_http_seconds':.001,'input_tokens':10,'output_tokens':20,'raw_response':{'choices':[{'message':{'content':json.dumps(a,ensure_ascii=False)}}]}}

def fake_stream(t,msgs,**kw):
 yield {'type':'delta','text':'요청 목적을 이해했습니다. 모의 도구 구성이며 실제 실행은 하지 않았습니다.'}
 yield {'type':'end','finish_reason':'stop','usage':{'prompt_tokens':12,'completion_tokens':14},'model_http_seconds':.002}

def wait(sid,rid):
 deadline=time.monotonic()+4
 while time.monotonic()<deadline:
  r=flow.get(sid,rid)
  if r['status']!='pending':return r
  time.sleep(.005)
 raise AssertionError('worker timeout')

def send(q='현재 온도는?',infer=fake_infer,stream=fake_stream):
 sid=store.create_session()['session_id'];rid=uuid.uuid4().hex
 with patch.object(llm_client,'probe',return_value={'can_run':True}),patch.object(llm_client,'infer',side_effect=infer) as first,patch.object(llm_client,'stream_response',side_effect=stream) as second:
  accepted,reused=flow.submit(sid,q,rid);r=wait(sid,rid)
 return accepted,r,first.call_count,second.call_count


def test_valid_two_stages_real_stream_contract():
 accepted,r,a,b=send();assert accepted['phase']=='accepted' and accepted['reply']==''
 assert r['status']=='completed' and a==b==1 and r['llm_call_count']==2
 assert 'reply' not in r['analysis_stage']['content'] and set(r['analysis'])=={'requests','memory'}
 assert r['mock_tools']['mode']=='mock' and r['actual_tool_calls']==0
 assert r['input_tokens']==22 and r['output_tokens']==34
 assert 'first_fragment_seconds' in r['timings'] and r['reply']==r['response_stage']['raw_content']
 msgs=flow.response_messages(r);assert 'tools' not in llm_client.response_payload(config.targets()['vllm_qwen'],msgs,max_tokens=spec.REPLY_TOKENS)[1]
 assert llm_client.response_payload(config.targets()['vllm_qwen'],msgs,max_tokens=spec.REPLY_TOKENS)[1]['stream'] is True

@pytest.mark.parametrize('iid',['out_of_scope','clarify'])
def test_server_response_gate(iid):
 def infer(t,msgs,**kw):
  r=fake_infer(t,msgs,**kw);u=json.loads(msgs[-1]['content']);r['content']=json.dumps(analysis(u['text'],u['turn_id'],iid),ensure_ascii=False);return r
 _,r,a,b=send(infer=infer)
 assert a==1 and b==(0 if iid=='out_of_scope' else 1)
 if iid=='out_of_scope':assert r['reply']=='' and r['status_message']=='지원하는 업무 의도를 찾지 못했습니다.'
 else:assert r['analysis']['requests'][0]['clarification_question'] and r['reply']

@pytest.mark.parametrize('bad',['source','schema','cutoff','api'])
def test_analysis_failure_never_calls_response(bad):
 def infer(t,msgs,**kw):
  if bad=='api':raise llm_client.ModelError('timeout','모델 시간 초과')
  r=fake_infer(t,msgs,**kw);a=json.loads(r['content'])
  if bad=='source':a['requests'][0]['evidence'][0]['quote']='없는 원문'
  if bad=='schema':a['reply']='분석에서 생성하면 안 되는 답변'
  if bad=='cutoff':r['finish_reason']='length'
  r['content']=json.dumps(a,ensure_ascii=False);return r
 _,r,a,b=send(infer=infer)
 assert r['status']=='error' and a==1 and b==0 and not r['reply'] and not r.get('analysis_validated') and r['response_generation_allowed'] is False
 assert 'analysis_stage' not in flow.public(r)


def test_no_exposure_before_validation_and_stable_idempotency():
 sid=store.create_session()['session_id'];rid=uuid.uuid4().hex;entered=threading.Event();release=threading.Event()
 def blocked(*a,**kw):entered.set();release.wait(3);return fake_infer(*a,**kw)
 with patch.object(llm_client,'probe',return_value={'can_run':True}),patch.object(llm_client,'infer',side_effect=blocked) as a,patch.object(llm_client,'stream_response',side_effect=fake_stream) as b:
  first,_=flow.submit(sid,'현재 온도는?',rid);assert entered.wait(2)
  duplicate,reused=flow.submit(sid,'현재 온도는?',rid);assert reused and first['message_id']==duplicate['message_id']
  assert not flow.public(duplicate).get('analysis') and not duplicate['reply'] and b.call_count==0
  with pytest.raises(flow.RequestConflict):flow.submit(sid,'다른 질문',rid)
  other=store.create_session()['session_id']
  with pytest.raises(flow.RequestConflict):flow.submit(other,'현재 온도는?',rid)
  with pytest.raises(store.SessionBusy):flow.submit(sid,'다른 질문',uuid.uuid4().hex)
  release.set();r=wait(sid,rid);again,reused=flow.submit(sid,'현재 온도는?',rid)
 assert len(store.session_history(sid))==1 and again['status']=='completed' and a.call_count==b.call_count==1


def test_partial_response_error_is_preserved_no_retry():
 def broken(*a,**kw):
  yield {'type':'delta','text':'모의 분석 설명의 일부 문장을 작성 중입니다. 실제 업무를 실행한 것은 아닙니다.'}
  raise llm_client.ModelError('stream_disconnected','모델 연결 중단')
 _,r,a,b=send(stream=broken)
 assert a==b==1 and r['status']=='error' and r['reply'] and r['response_stage']['raw_content'].startswith(r['reply'])
 assert r['output_tokens'] is None and r['response_stage']['content']==r['reply']
 assert r['error_kind']=='stream_disconnected'


def test_cancel_and_terminal_replay_never_regenerates():
 sid=store.create_session()['session_id'];rid=uuid.uuid4().hex;entered=threading.Event()
 def slow(*a,cancel,**kw):
  yield {'type':'delta','text':'모의 설명의 일부입니다. 아직 생성 중이며 실제 결과는 없습니다.'};entered.set()
  while not cancel.wait(.01):pass
  raise llm_client.ModelError('cancelled','사용자 중단')
 with patch.object(llm_client,'probe',return_value={'can_run':True}),patch.object(llm_client,'infer',side_effect=fake_infer),patch.object(llm_client,'stream_response',side_effect=slow) as second:
  flow.submit(sid,'현재 온도는?',rid);assert entered.wait(2);flow.cancel(sid,rid);r=wait(sid,rid)
  ev=list(flow.events(sid,rid));assert len(ev)==1 and json.loads(ev[0])['record']['status']=='interrupted'
 assert r['reply'] and r['status']=='interrupted' and second.call_count==1


def test_restart_recovers_new_pending_without_overwriting_partials():
 sid=store.create_session()['session_id'];rid=uuid.uuid4().hex;r={'session_id':sid,'request_id':rid,'created_at':store.now(),'question':'원문','status':'pending','phase':'response','reply':'저장된 일부','chat_flow_version':spec.VERSION,'llm_call_count':2}
 with store.db() as c:c.execute('insert into turns values(?,?,?,?,?,?)',(rid,sid,r['created_at'],'원문','pending',json.dumps(r)))
 store.recover_interrupted();out=flow.get(sid,rid);assert out['status']=='interrupted' and out['reply']=='저장된 일부' and out['error_kind']=='server_restarted'
 assert out['llm_call_count']==2


def test_memory_replace_withdraw_new_task_and_source_isolation():
 sources={'old':{'text':'포장기 네 대','session_id':'s','role':'user'},'cur':{'text':'포장기는 두 대로 정정할게.','session_id':'s','role':'user'}}
 old=[{'slot':'equipment_count','value':'포장기 4대','evidence':[{'turn_id':'old','quote':'포장기 네 대'}]}]
 a=analysis(sources['cur']['text'],'cur','work.plan',{'relation':'correct','updates':[{'slot':'equipment_count','value':'포장기 2대','evidence':[{'turn_id':'cur','quote':sources['cur']['text']}]}],'remove':[]})
 _,active=chat_analysis.validate(json.dumps(a,ensure_ascii=False),'stop',[],sources,session_id='s',current_turn_id='cur',prior_memory=old)
 assert [x['value'] for x in active]==['포장기 2대'] and old[0]['value']=='포장기 4대'
 a['memory']={'relation':'withdraw','updates':[],'remove':[{'slot':'equipment_count','evidence':[{'turn_id':'cur','quote':sources['cur']['text']}]}]}
 assert chat_analysis.validate(json.dumps(a),'stop',[],sources,session_id='s',current_turn_id='cur',prior_memory=active)[1]==[]
 a['memory']={'relation':'new_task','updates':[],'remove':[]}
 assert chat_analysis.validate(json.dumps(a),'stop',[],sources,session_id='s',current_turn_id='cur',prior_memory=old)[1]==[]
 a['requests'][0]['evidence']=[{'turn_id':'old','quote':'포장기 네 대'}]
 with pytest.raises(ContractError):chat_analysis.validate(json.dumps(a),'stop',[],sources,session_id='s',current_turn_id='cur',prior_memory=old)
 with pytest.raises(ContractError):chat_analysis.validate(json.dumps(a),'stop',[],sources,session_id='other',current_turn_id='cur',prior_memory=old)


def test_history_limits_and_no_business_results_memory():
 _,r,_,_=send();msgs,lim,src,mem=flow.analysis_messages({**r,'request_id':'new','question':'어제 기록이야'},[r])
 assert len(src)==2 and lim['verified_business_results_available'] is False and lim['concrete_system_proposals_available'] is False
 assert 'previous_interpretations' in msgs[0]['content'] and r['reply'] not in json.dumps(msgs,ensure_ascii=False)
 with pytest.raises(ContractError):flow.analysis_messages({**r,'session_id':'other','request_id':'new'},[r])


def test_compound_outside_not_forwarded_and_mock_execution_disabled():
 _,r,_,_=send();r['analysis']['requests'].append({**r['analysis']['requests'][0],'intent_id':'out_of_scope','interpreted_request':'주식 매수 추천'});r['mock_tools']=mock_catalog.connections(r['analysis']['requests'])
 payload=flow.response_messages(r)[1]['content'];assert '주식 매수' not in payload and 'out_of_scope' not in payload
 catalog=mock_catalog.load();assert all(not t['implemented'] and not t['execution_enabled'] and not t['write_enabled'] for t in catalog['tools'])
 assert all(t['approval_required'] and not t['approved'] for t in catalog['tools'] if t['id'] in ['simulation.preview','proposal.request','twin.change'])


def test_export_model_labels_not_gold_and_sessions_persist():
 _,r,_,_=send();r['Authorization']='secret';r['api_key']='secret';store.save_record(r)
 exported=json.loads(next(flow.export(r['session_id'])));assert not exported['labels_are_gold'] and exported['human_label'] is None and exported['review_status']=='unreviewed'
 assert exported['request_id']==r['request_id'] and exported['model_labels'] and exported['user_text']==r['question']
 assert 'secret' not in json.dumps(exported) and exported['mock_mode']=='mock'
 assert flow.sessions()[0]['messages']==1
 assert store.session_history(r['session_id'])[0]['reply']==r['reply']


def test_actual_vllm_sse_parser_ignores_reasoning_and_preserves_korean():
 def data(x):return 'data: '+json.dumps(x,ensure_ascii=False)+'\n\n'
 wire=''.join([data({'choices':[{'delta':{'reasoning_content':'노출 금지'},'finish_reason':None}]}),data({'choices':[{'delta':{'content':'한'},'finish_reason':None}]}),data({'choices':[{'delta':{'content':'국어 설명'},'finish_reason':'stop'}]}),data({'choices':[],'usage':{'prompt_tokens':11,'completion_tokens':5}}),'data: [DONE]\n\n']).encode()
 with patch('urllib.request.urlopen',return_value=io.BytesIO(wire)) as post:
  events=list(llm_client.stream_response(config.targets()['vllm_qwen'],[{'role':'user','content':'x'}],max_tokens=12,cancel=threading.Event()))
 assert ''.join(e.get('text','') for e in events)=='한국어 설명' and '노출 금지' not in json.dumps(events,ensure_ascii=False)
 assert post.call_count==1 and events[-1]['usage']['prompt_tokens']==11
 body=json.loads(post.call_args.args[0].data);assert body['stream'] and 'tools' not in body and 'response_format' not in body


def test_api_session_request_isolation_and_required_client_id():
 client=TestClient(app);sid=client.post('/api/chat/sessions').json()['session_id']
 assert client.post(f'/api/chat/sessions/{sid}/messages',json={'question':'x'}).status_code==422
 assert client.post(f'/api/chat/sessions/{sid}/messages',json={'question':' ','request_id':uuid.uuid4().hex}).status_code==422
 assert client.get(f'/api/chat/sessions/{sid}/messages/nope').status_code==404
 assert client.get('/api/chat/sessions?limit=0').status_code==422
 assert client.get(f'/api/chat/sessions/{sid}/export').status_code==200


def test_closing_completed_stream_never_mutates_finished_record():
 _,r,_,_=send();before=json.dumps(flow.get(r['session_id'],r['request_id']),ensure_ascii=False)
 flow.cancel(r['session_id'],r['request_id'],'완료 연결 정리')
 assert json.dumps(flow.get(r['session_id'],r['request_id']),ensure_ascii=False)==before


def test_snapshot_and_delta_do_not_replay_existing_partial_text():
 sid=store.create_session()['session_id'];rid=uuid.uuid4().hex;entered=threading.Event();release=threading.Event()
 def slow(*a,**kw):
  yield {'type':'delta','text':'이미 생성된 한국어 문장 부분입니다.'};entered.set();release.wait(3)
  yield {'type':'delta','text':' 이어서 생성된 나머지 설명입니다.'}
  yield {'type':'end','finish_reason':'stop','usage':{'prompt_tokens':1,'completion_tokens':2},'model_http_seconds':.01}
 with patch.object(llm_client,'probe',return_value={'can_run':True}),patch.object(llm_client,'infer',side_effect=fake_infer),patch.object(llm_client,'stream_response',side_effect=slow):
  flow.submit(sid,'현재 온도는?',rid);assert entered.wait(2);iterator=flow.events(sid,rid);snap=json.loads(next(iterator));assert snap['type']=='snapshot' and snap['record']['reply'];text=snap['record']['reply'];release.set()
  for raw in iterator:
   e=json.loads(raw)
   if e['type']=='delta':text+=e['text']
  final=wait(sid,rid);assert text==final['reply']


def test_http_metrics_sum_and_partial_http_unknown():
 _,r,_,_=send();assert r['analysis_http_seconds']==.001 and r['response_http_seconds']==.002 and r['model_http_seconds']==.003


def test_response_cutoff_preserves_partial_and_is_not_completed():
 def cutoff(*a,**kw):
  yield {'type':'delta','text':'검증된 요청 의도를 설명하는 중인 부분 응답입니다.'}
  yield {'type':'end','finish_reason':'length','usage':{'prompt_tokens':2,'completion_tokens':8},'model_http_seconds':.01}
 _,r,a,b=send(stream=cutoff);assert r['status']=='error' and r['error_kind']=='truncated' and r['reply'] and a==b==1
 assert r['response_stage']['finish_reason']=='length' and r['response_stage']['raw_content']==r['reply']


def test_reasoning_content_marker_split_does_not_leak():
 def marked(*a,**kw):
  yield {'type':'delta','text':'<thi'}
  yield {'type':'delta','text':'nk>내부 추론 문장'}
 _,r,a,b=send(stream=marked);assert r['status']=='error' and r['error_kind']=='unexpected_reasoning' and r['reply']=='' and a==b==1
 assert '내부 추론 문장' in r['response_stage']['raw_content'] and 'raw_content' not in flow.public(r)['response_stage']


def test_new_task_explicit_cleanup_is_not_inheritance_and_is_atomic():
 sources={'old':{'text':'제품 A를 내일 포장기 2대로 계획','session_id':'s','role':'user'},'cur':{'text':'다른 업무야. 혼합기 세척 방법을 문서에서 찾아줘.','session_id':'s','role':'user'}}
 old=[{'slot':slot,'value':value,'evidence':[{'turn_id':'old','quote':sources['old']['text']}]} for slot,value in [('product','제품 A'),('date','내일'),('machines','2대')]]
 original=json.dumps(old,ensure_ascii=False)
 a=analysis(sources['cur']['text'],'cur','knowledge.lookup',{'relation':'new_task','updates':[{'slot':'target','value':'혼합기 세척','evidence':[{'turn_id':'cur','quote':'혼합기 세척 방법'}]}],'remove':[{'slot':f['slot'],'evidence':[{'turn_id':'cur','quote':'다른 업무야.'}]} for f in old]})
 _,active=chat_analysis.validate(json.dumps(a),'stop',[],sources,session_id='s',current_turn_id='cur',prior_memory=old)
 assert [f['slot'] for f in active]==['target'] and json.dumps(old,ensure_ascii=False)==original
 a['memory']['remove'][0]['slot']='nonexistent'
 with pytest.raises(ContractError,match='기존 활성 슬롯'):chat_analysis.validate(json.dumps(a),'stop',[],sources,session_id='s',current_turn_id='cur',prior_memory=old)
 assert json.dumps(old,ensure_ascii=False)==original
 a['memory']['remove']=[];a['memory']['updates'][0]['evidence']=[{'turn_id':'old','quote':sources['old']['text']}]
 with pytest.raises(ContractError):chat_analysis.validate(json.dumps(a),'stop',[],sources,session_id='s',current_turn_id='cur',prior_memory=old)
 assert json.dumps(old,ensure_ascii=False)==original


@pytest.mark.parametrize('invalid_json',[False,True])
def test_terminal_analysis_failure_is_audit_not_validated_output(invalid_json):
 def infer(t,msgs,**kw):
  raw=fake_infer(t,msgs,**kw)
  if invalid_json:raw['content']='{"requests":'
  else:
   obj=json.loads(raw['content']);obj['memory']['remove']=[{'slot':'not_present','evidence':obj['requests'][0]['evidence']}];raw['content']=json.dumps(obj,ensure_ascii=False)
  return raw
 _,r,a,b=send(infer=infer)
 assert r['status']=='error' and a==1 and b==0 and not r['analysis_validated'] and not r.get('memory_snapshot')
 audit=flow.public(r)['analysis_failure']
 assert audit['model_output']==r['analysis_stage']['content'] and not audit['validated']
 assert audit['parse_status']==('invalid_json' if invalid_json else 'parsed') and audit['validation_failure']['message']==r['error']
 exported=json.loads(next(flow.export(r['session_id'])))['record'];assert exported['analysis_failure']==audit
 pending={**r,'status':'pending'};assert 'analysis_failure' not in flow.public(pending) and 'analysis_stage' not in flow.public(pending)
 # Reading a legacy failure uses the same diagnostic helper without mutating storage.
 legacy=dict(r);legacy.pop('analysis_failure');before=json.dumps(legacy,ensure_ascii=False)
 assert flow.public(legacy)['analysis_failure']==audit and json.dumps(legacy,ensure_ascii=False)==before
 store.save_record(legacy)
 assert json.loads(next(flow.export(r['session_id'])))['record']['analysis_failure']==audit
 assert 'analysis_failure' not in flow.get(r['session_id'],r['request_id'])


def test_heartbeat_and_status_are_not_first_body_timing():
 def none(*args,**kwargs):
  yield {'type':'end','finish_reason':'stop','usage':{},'model_http_seconds':.001}
 _,r,_,_=send(stream=none)
 assert r['status']=='error' and 'first_fragment_seconds' not in r['timings'] and 'response_first_fragment_seconds' not in r['timings']


def test_failed_followup_cannot_partially_commit_memory():
 def first(t,msgs,**kw):
  raw=fake_infer(t,msgs,**kw);obj=json.loads(raw['content']);obj['memory']['updates']=[{'slot':'machines','value':'2대','evidence':obj['requests'][0]['evidence']}];raw['content']=json.dumps(obj,ensure_ascii=False);return raw
 _,prior,_,_=send(q='포장기는 2대',infer=first);before=json.dumps(flow.get(prior['session_id'],prior['request_id']),ensure_ascii=False)
 def broken(t,msgs,**kw):
  raw=fake_infer(t,msgs,**kw);obj=json.loads(raw['content']);obj['memory']={'relation':'correct','updates':[{'slot':'machines','value':'3대','evidence':obj['requests'][0]['evidence']}],'remove':[{'slot':'absent','evidence':obj['requests'][0]['evidence']}]};raw['content']=json.dumps(obj,ensure_ascii=False);return raw
 rid=uuid.uuid4().hex
 with patch.object(llm_client,'probe',return_value={'can_run':True}),patch.object(llm_client,'infer',side_effect=broken),patch.object(llm_client,'stream_response',side_effect=fake_stream) as response:
  flow.submit(prior['session_id'],'3대로 정정','%s'%rid);failed=wait(prior['session_id'],rid)
 assert failed['status']=='error' and response.call_count==0 and not failed.get('memory_snapshot')
 assert failed['memory_previous'][0]['value']=='2대' and json.dumps(flow.get(prior['session_id'],prior['request_id']),ensure_ascii=False)==before
 _,_,_,active=flow.analysis_messages({**failed,'request_id':'next','question':'그대로'},[prior,failed]);assert active[0]['value']=='2대'
