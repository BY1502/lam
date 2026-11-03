"""Automatic set/contract/timing audit + explicitly separate saved AI semantic reviews."""
import csv,hashlib,json,re,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'evaluation/results/compound-20261103-01'
FIELDS=['request_intent_link','request_separation','context_conditions','execution_facts','korean','scope_clarification']
def rows(name):return [json.loads(l) for l in (OUT/name).read_text().splitlines()] if (OUT/name).exists() else []
def load(name):return json.loads((OUT/name).read_text())
def write(name,x):(OUT/name).write_text(json.dumps(x,ensure_ascii=False,indent=2))
def csvfile(name,data):
 if not data:return
 with (OUT/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader()
  for r in data:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in r.items()})
def stats(values):
 v=sorted(x for x in values if isinstance(x,(int,float)))
 def pct(p):
  if not v:return None
  k=(len(v)-1)*p;i=int(k);return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(k-i)
 return {'n':len(v),'p50':pct(.5),'p95':pct(.95),'min':min(v) if v else None,'max':max(v) if v else None}
def aggregate(data):
 n=len(data)
 return {'n':n,'exact_intent_set':{'numerator':sum(x['exact_intent_set'] for x in data),'denominator':n},
         'missing_intent_instances':sum(len(x['missing_ids']) for x in data),'added_intent_instances':sum(len(x['added_ids']) for x in data),
         'duplicate_intent_instances':sum(x['duplicate_ids'] for x in data),'completed':sum(x['status']=='completed' for x in data),
         'errors':dict(Counter(x['error_kind'] or x['status'] for x in data if x['status']!='completed')),
         'semantic_review':{k:{v:sum(x[k]==v for x in data) for v in ['통과','부분 통과','실패','근거 부족','미검토']} for k in FIELDS},
         'strict_intersection_pass':sum(x['exact_intent_set'] and all(x[k]=='통과' for k in FIELDS) for x in data),
         'tokens':{k:{'sum_known':sum(x[k] for x in data if isinstance(x[k],int)),'known_n':sum(isinstance(x[k],int) for x in data)} for k in ['analysis_input_tokens','analysis_output_tokens','response_input_tokens','response_output_tokens']},
         'completed_with_body_latency':stats([x['total_seconds'] for x in data if x['status']=='completed' and x['reply_characters']>0]),
         'completed_first_body_latency':stats([x['first_body_seconds'] for x in data if x['status']=='completed' and x['reply_characters']>0]),
         'client_stored_done_confirmed':stats([x['client_total_seconds'] for x in data if x['status']=='completed']),'exact_id_success_latency':stats([x['total_seconds'] for x in data if x['exact_intent_set']]),
         'client_first_body_observed':stats([x['client_first_body_observed_seconds'] for x in data if x['status']=='completed']),'client_completed_observed':stats([x['client_completed_observed_seconds'] for x in data if x['status']=='completed']),'all_stage_times':{key:stats([x[key] for x in data if x['status']=='completed']) for key in ['queue_seconds','analysis_seconds','response_seconds','analysis_complete_event_observed_seconds']}}
def main():
 gold=load('questions_gold.json');cases={q['id']:q for q in gold['items']};raw=rows('runs.jsonl');evaluated=[r for r in raw if r['phase']=='evaluation'];review_rows=rows('reviews.jsonl');reviews={r['key']:r for r in review_rows};assert len(reviews)==len(review_rows)
 scored=[];message_metrics=[];audits=[];language=[]
 all_messages=raw+rows('warmup.jsonl')+rows('control.jsonl')
 allowed_users=defaultdict(dict)
 for x in all_messages:
  r=x.get('record') or {}
  if r:allowed_users[r['session_id']][r['request_id']]=r['question']
 for x in all_messages:
  r=x.get('record') or {};a=r.get('analysis_stage') or {};b=r.get('response_stage') or {};usage=b.get('usage') or {};t=r.get('timings') or {};events=x.get('events') or []
  observed=next((e['received_seconds'] for e in events if e['type']=='analysis'),None)
  metric={'key':x['key'],'phase':x.get('phase','separate_control'),'question_id':x.get('question_id'),'repeat':x.get('repeat'),'request_id':r.get('request_id'),'session_id':r.get('session_id'),'status':r.get('status','not_saved'),'error_kind':r.get('error_kind'),'error_stage':r.get('error_stage'),'error':r.get('error'),'queue_seconds':t.get('queue_seconds'),'analysis_seconds':t.get('analysis_seconds'),'analysis_complete_event_observed_seconds':observed,'server_analysis_complete_seconds':None,'first_body_seconds':t.get('first_fragment_seconds'),'response_seconds':t.get('response_seconds'),'total_seconds':t.get('total_seconds'),'client_total_seconds':None if x.get('browser_key') else x.get('client_total_seconds'),'browser_operation_with_readback_seconds':x.get('client_total_seconds') if x.get('browser_key') else None,'client_first_body_observed_seconds':next((e['received_seconds'] for e in events if e['type']=='delta' and e.get('text')),None),'client_completed_observed_seconds':next((e['received_seconds'] for e in events if e['type'] in ['complete','error']),None),'timing_origin':'백엔드 timings는 SQLite저장 수락 뒤 Job등록시작점. HTTP소켓수신시각 아님. 클라이언트송신/이벤트수신·UI시간 별도.','ui_first_body_seconds':x.get('ui_first_body_seconds'),'ui_completed_seconds':x.get('ui_completed_seconds'),'analysis_http_seconds':a.get('model_http_seconds'),'response_http_seconds':b.get('model_http_seconds'),'analysis_input_tokens':a.get('input_tokens'),'analysis_output_tokens':a.get('output_tokens'),'response_input_tokens':usage.get('prompt_tokens'),'response_output_tokens':usage.get('completion_tokens'),'analysis_finish_reason':a.get('finish_reason'),'response_finish_reason':b.get('finish_reason'),'generation_calls':r.get('llm_call_count',0),'analysis_calls':r.get('analysis_call_count',0),'response_calls':r.get('response_call_count',0),'reply_characters':len(r.get('reply') or ''),'validation_checks':r.get('validation_checks')}
  message_metrics.append(metric)
  current_users=allowed_users.get(r.get('session_id'),{});messages=(r.get('request') or {}).get('messages') or [];source_ok=True;bad=[]
  for m in messages:
   if m['role']!='user':continue
   try:u=json.loads(m['content'])
   except ValueError:source_ok=False;bad.append('user_message_not_json');continue
   if current_users.get(u.get('turn_id'))!=u.get('text'):source_ok=False;bad.append(u.get('turn_id'))
  # The current question and bounded prior user messages only; no gold/expected fields are supplied.
  no_gold_user_fields=True
  for m in messages:
   if m['role']=='user':
    try:no_gold_user_fields &= set(json.loads(m['content']))=={'turn_id','text','source_type'}
    except (ValueError,TypeError):no_gold_user_fields=False
  before_body=True;validated=False
  for e in events:
   if e['type']=='analysis' or e.get('record',{}).get('analysis_validated'):validated=True
   if e['type']=='delta' and e.get('text') and not validated:before_body=False
  audits.append({'key':x['key'],'same_session_user_sources':source_ok,'bad_source_turns':bad,'gold_fields_not_in_user_messages':no_gold_user_fields,'analysis_tools_absent':'tools' not in r.get('request',{}),'response_tools_absent':'tools' not in b.get('request',{}),'actual_tool_calls':r.get('actual_tool_calls',0),'calls_at_most_two':r.get('llm_call_count',0)<=2,'terminal_event_received':any(e['type'] in ['complete','error'] for e in events) if events else None,'stream_eof_record_terminal':r.get('status')!='pending' if events else None,'validated_before_body_events':before_body if events else None})
  if x.get('phase')!='evaluation':continue
  g=cases[x['question_id']];analysis=r.get('analysis') or {};ids=[q['intent_id'] for q in analysis.get('requests',[])];expected=set(g['expected_ids']);actual=set(ids);rev=reviews.get(x['key'],{});normal=r.get('status')=='completed'
  parsed=(r.get('analysis_failure') or {}).get('parsed_analysis');unvalidated_ids=[q.get('intent_id') for q in parsed.get('requests',[]) if isinstance(q,dict)] if isinstance(parsed,dict) else []
  result={**metric,'group':g['group'],'question':g['question'],'expected_ids':sorted(expected),'actual_ids':sorted(actual),'unvalidated_generated_intent_ids':unvalidated_ids,'intent_sequence':ids,'missing_ids':sorted(expected-actual),'added_ids':sorted(actual-expected),'duplicate_ids':len(ids)-len(actual),'analysis_id_set_matches':bool(r.get('analysis_validated') and expected==actual),'exact_intent_set':bool(normal and r.get('analysis_validated') and expected==actual),**{k:rev.get(k,'미검토') for k in FIELDS},'ai_review_note':rev.get('note'),'language_mixing':rev.get('language_mixing',[]),'reviewer':rev.get('reviewer')};scored.append(result)
  generated=[('reply',r.get('reply') or '')]
  review_analysis=analysis or (parsed if isinstance(parsed,dict) else {})
  for i,q in enumerate(review_analysis.get('requests',[])):
   for k in ['interpreted_request','reason','time_expression','clarification_question']:
    if q.get(k):generated.append((f'requests[{i}].{k}',q[k]))
   for k in ['target_expressions','conditions','missing_information']:
    for j,s in enumerate(q.get(k,[])):generated.append((f'requests[{i}].{k}[{j}]',s))
  for i,f in enumerate(r.get('memory_snapshot',[])):generated.append((f'memory[{i}].value',f['value']))
  candidates=0
  for field,text in generated:
   hits=re.findall(r'[\u3400-\u9fff\u3040-\u30ff]+|[A-Za-z][A-Za-z._-]{2,}',text)
   if not hits:continue
   candidates+=1;language.append({'key':x['key'],'field':field,'automatic_candidates':hits,'full_context':text,'ai_judgment':'허용 표현' if rev.get('korean')=='통과' else '검토 메모 참조' if rev else '미검토','ai_note':rev.get('note'),'actual_mixing':rev.get('language_mixing',[]),'method':'문자검출은 후보만. 개발AI가 원본 분석/본문/활성조건 전체를 별도 검토.'})
  if not candidates:language.append({'key':x['key'],'field':'all_generated_fields','automatic_candidates':[],'full_context':'문자 후보 없음; 원본 의미 검토는 별도 수행','ai_judgment':'실제 혼입 관찰 안 됨' if rev.get('korean')=='통과' else '미검토','ai_note':rev.get('note'),'actual_mixing':rev.get('language_mixing',[]),'method':'문자 검출만으로 한국어 적합성을 확정하지 않음.'})
 scored.sort(key=lambda r:(r['question_id'],r['repeat']));csvfile('scored.csv',scored);csvfile('message_metrics.csv',message_metrics);csvfile('language_review.csv',language);write('contract_audit.json',audits)
 perquestion=[]
 for q in gold['items']:
  rr=[r for r in scored if r['question_id']==q['id']];signature=[tuple(r['actual_ids']) for r in rr]
  perquestion.append({'question_id':q['id'],'group':q['group'],'expected_ids':q['expected_ids'],'runs':[{'repeat':r['repeat'],'actual_ids':r['actual_ids'],'status':r['status'],'exact':r['exact_intent_set'],'semantic':{k:r[k] for k in FIELDS}} for r in rr],'exact_count':sum(r['exact_intent_set'] for r in rr),'denominator':3,'intent_set_repeat_consistent':len(rr)==3 and len(set(signature))==1,'all_three_normal':len(rr)==3 and all(r['status']=='completed' for r in rr)})
 write('question_results.json',perquestion)
 summary={'planned_evaluation_cases':72,'saved_evaluation_cases':len(scored),'saved_preparation_messages':sum(x.get('phase')=='preparation' for x in raw),'evaluation_dialog_user_messages':len(raw),'warmup_user_messages':len(rows('warmup.jsonl')),'outside_control_user_messages':len(rows('control.jsonl')),'all_recorded_user_messages':len(all_messages),'generation_calls':sum(m['generation_calls'] for m in message_metrics),'generation_calls_evaluation':sum(m['generation_calls'] for m in message_metrics if m['phase']=='evaluation'),'generation_calls_preparation':sum(m['generation_calls'] for m in message_metrics if m['phase']=='preparation'),'generation_calls_separate':sum(m['generation_calls'] for m in message_metrics if m['phase']=='separate_control'),'overall':aggregate(scored),'by_repeat':{str(rep):aggregate([r for r in scored if r['repeat']==rep]) for rep in [1,2,3]},'by_group':{g:aggregate([r for r in scored if r['group']==g]) for g in sorted({q['group'] for q in gold['items']})},'stream_termination':{'api_streams':sum(bool(x.get('events')) for x in all_messages),'terminal_events_received':sum(any(e['type'] in ['complete','error'] for e in x.get('events',[])) for x in all_messages),'client_errors':sum(bool(x.get('client_errors')) for x in all_messages),'browser_direct_terminal_event_measurement':'미측정; DOM/최종저장상태만확인'},'reviewed_cases':len([r for r in scored if r['request_intent_link']!='미검토']),'question_consistent':sum(x['intent_set_repeat_consistent'] for x in perquestion),'question_all_three_exact':sum(x['exact_count']==3 for x in perquestion),'formal_analysis_json':{'provided':sum(bool((x.get('record') or {}).get('analysis_stage')) for x in all_messages),'parseable':sum(_parseable(((x.get('record') or {}).get('analysis_stage') or {}).get('content')) for x in all_messages)},'formal_validation':{'format_passed':sum(((x.get('record') or {}).get('validation_checks') or {}).get('format')=='passed' for x in all_messages),'source_passed':sum(((x.get('record') or {}).get('validation_checks') or {}).get('source')=='passed' for x in all_messages),'message_denominator':len(all_messages)},'timeouts_all_messages':sum((x.get('record') or {}).get('error_kind')=='timeout' for x in all_messages),'all_message_statuses':dict(Counter((x.get('record') or {}).get('status','not_saved') for x in all_messages)),'token_metrics':{k:{'sum_known':sum(m[k] for m in message_metrics if isinstance(m[k],int)),'known_messages':sum(isinstance(m[k],int) for m in message_metrics)} for k in ['analysis_input_tokens','analysis_output_tokens','response_input_tokens','response_output_tokens']},'measurement_limit':'서버 수신→분석 완료의 독립 타임스탬프는 현 구현에 없어 미측정(null). API클라이언트 송신→analysis 이벤트 수신을 관측값으로 별도 기록; 대기+분석 시간을 정확한 완료 시각으로 추정하지 않음. 첫본문은 실제 nonempty delta/DOM; 상태/heartbeat 제외. UI시간은 사전지정3표본만.'};write('summary.json',summary)
 write('failures.json',[{**r,'record_file':'runs.jsonl','requests':((next((x for x in evaluated if x['key']==r['key']),{}).get('record') or {}).get('analysis') or {}).get('requests'),'reply':(next((x for x in evaluated if x['key']==r['key']),{}).get('record') or {}).get('reply')} for r in scored if not r['exact_intent_set'] or any(r[k] not in ['통과','미검토'] for k in FIELDS)])
 print(json.dumps({'saved':len(scored),'exact':summary['overall']['exact_intent_set'],'reviewed':summary['reviewed_cases'],'calls':summary['generation_calls']},ensure_ascii=False))
def _parseable(value):
 if not isinstance(value,str):return False
 try:json.loads(value);return True
 except ValueError:return False
if __name__=='__main__':main()
