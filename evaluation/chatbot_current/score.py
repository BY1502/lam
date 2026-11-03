"""Fixed-gold metrics; separate automatic checks and explicit AI reading judgments."""
import csv,hashlib,json,math,re,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from evaluation.score import percentile,ratio
from evaluation.chatbot_current.review import export,generated,rows
from backend import config
OUT=ROOT/'evaluation/results/chatbot-eval-20261029-01'

def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def timing(rs,key):
 vals=[r['record'][key] for r in rs if isinstance(r['record'].get(key),(int,float))]
 return {'n':len(vals),'p50':percentile(vals,.5),'p95':percentile(vals,.95),'min':min(vals) if vals else None,'max':max(vals) if vals else None}
def good(r):return r['record'].get('status')=='ok' and isinstance(r['record'].get('analysis'),dict)
def ids(r):return [x['intent_id'] for x in r['record']['analysis']['requests']] if good(r) else []
def correct(r,gold):return good(r) and set(ids(r))==set(gold[r['case_id']]['expected_ids'] or [])
def corr(xs,ys):
 if len(xs)<3:return None
 mx=sum(xs)/len(xs);my=sum(ys)/len(ys);d=(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))**.5
 return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/d if d else None

def main():
 groups=export();data=rows(OUT/'runs.jsonl');gold={x['id']:x for x in json.loads((OUT/'questions_gold.json').read_text())['cases']};reviews={r['group_id']:r for r in rows(OUT/'manual_reviews.jsonl')};member={k:g for g in groups for k in g['members']};by_request={r['record'].get('request_id'):r for r in data if r['record'].get('request_id')};manifest=json.loads((OUT/'manifest.json').read_text())
 if any(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()!=h for f,h in manifest['hashes'].items()):raise RuntimeError('고정 앱/입력 해시 변경')
 def metric(rs,scored=True):
  valid=[r for r in rs if good(r)];failed=[r for r in rs if not good(r)]
  result={'n':len(rs),'valid':len(valid),'errors':len(failed),'truncated':sum(r['record'].get('error_kind')=='truncated' for r in rs),'timeouts':sum(r['record'].get('error_kind')=='timeout' for r in rs),'error_kinds':dict(Counter(r['record'].get('error_kind') for r in failed)),'backend_all':timing(rs,'elapsed_seconds'),'http_all':timing(rs,'model_http_seconds'),'backend_success':timing(valid,'elapsed_seconds'),'backend_failure':timing(failed,'elapsed_seconds'),'http_success':timing(valid,'model_http_seconds'),'http_failure':timing(failed,'model_http_seconds'),'input_tokens':sum(r['record'].get('input_tokens') or 0 for r in rs),'output_tokens':sum(r['record'].get('output_tokens') or 0 for r in rs),'reasoning_tokens':None if all(r['record'].get('reasoning_tokens') is None for r in rs) else sum(r['record'].get('reasoning_tokens') or 0 for r in rs)}
  if scored:
   right=[r for r in rs if correct(r,gold)];wrong=[r for r in rs if not correct(r,gold)]
   result.update(correct=len(right),ordered_correct=sum(good(r) and ids(r)==gold[r['case_id']]['expected_ids'] for r in rs),backend_id_correct=timing(right,'elapsed_seconds'),backend_id_incorrect=timing(wrong,'elapsed_seconds'),http_id_correct=timing(right,'model_http_seconds'),http_id_incorrect=timing(wrong,'model_http_seconds'))
  reviewed=[reviews[member[f"{r['repeat']}:{r['case_id']}"]['id']] for r in rs if member[f"{r['repeat']}:{r['case_id']}"]['id'] in reviews]
  result['semantic_review']={'reviewed':len(reviewed),'language':dict(Counter(v['language_judgment'] for v in reviewed)),'explanation':dict(Counter(v['explanation_judgment'] for v in reviewed)),'id_reply_mismatch':sum(v['id_reply_mismatch'] for v in reviewed),'unsupported_fact':sum(v['unsupported_fact'] for v in reviewed),'scope_escape':sum(v['scope_escape'] for v in reviewed),'split':dict(Counter(v['split_judgment'] for v in reviewed)),'context':dict(Counter(v['context_judgment'] for v in reviewed))}
  return result
 scored=[r for r in data if gold[r['case_id']]['scored']];by_suite={s:metric([r for r in data if r['suite']==s],s!='policy_review') for s in ['single','boundary_complex','conversation','policy_review']};by_suite['scored_total']=metric(scored);by_suite['all_requests']=metric(data,False)
 special={}
 for label in ['clarify','out_of_scope']:
  truth=[r for r in scored if label in gold[r['case_id']]['expected_ids']];pred=[r for r in scored if label in ids(r)];tp=sum(label in gold[r['case_id']]['expected_ids'] for r in pred)
  special[label]={'true_positive':tp,'predicted':len(pred),'expected':len(truth),'precision':ratio(tp,len(pred)),'recall':ratio(tp,len(truth))}
 inside=[r for r in scored if not set(gold[r['case_id']]['expected_ids'])&{'clarify','out_of_scope'}]
 seq=defaultdict(list)
 for r in data:seq[r['case_id']].append(r)
 consistency={s:{'full_three':sum(len(rs)==3 for rs in seq.values() if rs[0]['suite']==s),'same_ids_status':sum(len(rs)==3 and len({(r['record']['status'],tuple(ids(r))) for r in rs})==1 for rs in seq.values() if rs[0]['suite']==s),'all_three_correct':sum(len(rs)==3 and all(correct(r,gold) for r in rs) for rs in seq.values() if rs[0]['suite']==s and gold[rs[0]['case_id']]['scored'])} for s in by_suite if s not in ['all_requests','scored_total']}
 composite=[r for r in scored if len(gold[r['case_id']]['expected_ids'])>1];predids=sum(len(set(ids(r))) for r in composite);trueids=sum(len(set(gold[r['case_id']]['expected_ids'])) for r in composite)
 compound={'n':len(composite),'exact_set':sum(correct(r,gold) for r in composite),'missing_ids':sum(len(set(gold[r['case_id']]['expected_ids'])-set(ids(r))) for r in composite),'expected_ids':trueids,'extra_ids':sum(len(set(ids(r))-set(gold[r['case_id']]['expected_ids'])) for r in composite),'predicted_ids':predids,'runs_with_missing':sum(bool(set(gold[r['case_id']]['expected_ids'])-set(ids(r))) for r in composite),'runs_with_extra':sum(bool(set(ids(r))-set(gold[r['case_id']]['expected_ids'])) for r in composite),'duplicate_id_items':sum(len(ids(r))-len(set(ids(r))) for r in data)}
 conversations=defaultdict(list)
 for r in data:
  if r['conversation_id']:conversations[(r['repeat'],r['conversation_id'])].append(r)
 context_audit=[]
 for r in data:
  messages=r['record'].get('request',{}).get('messages',[]);bad=[]
  for msg in messages[1:-1]:
   if msg.get('role')=='user':
    try:
     value=json.loads(msg['content']);parent=by_request.get(value['turn_id'])
     if not parent or parent['session_id']!=r['session_id'] or gold[parent['case_id']]['question']!=value['text']:bad.append(value)
    except Exception:bad.append({'unparseable_user_message':msg['content']})
  if bad:context_audit.append({'repeat':r['repeat'],'case_id':r['case_id'],'foreign_or_invalid_context':bad})
 dialogue={'independent_dialogues':10,'dialogue_runs_completed':sum(len(v)==3 for v in conversations.values()),'all_three_turns_id_correct':sum(len(v)==3 and all(correct(r,gold) for r in v) for v in conversations.values()),'by_turn':{str(t):metric([r for r in data if r['suite']=='conversation' and r['turn']==t]) for t in [1,2,3]},'foreign_context_runs':context_audit}
 labels=[*config.INTENT_IDS,'MULTI','ERROR'];matrix={x:{y:0 for y in labels} for x in config.INTENT_IDS}
 for r in data:
  if r['suite']!='single':continue
  expected=gold[r['case_id']]['expected_ids'][0];actual='ERROR' if not good(r) else next(iter(set(ids(r)))) if len(set(ids(r)))==1 else 'MULTI';matrix[expected][actual]+=1
 with (OUT/'confusion_matrix.csv').open('w',newline='') as f:w=csv.writer(f);w.writerow(['expected/actual',*labels]);w.writerows([i,*[matrix[i][j] for j in labels]] for i in config.INTENT_IDS)
 per_intent={i:metric([r for r in data if r['suite']=='single' and gold[r['case_id']]['expected_ids']==[i]]) for i in config.INTENT_IDS}
 failures=[];scored_rows=[];language_rows=[]
 foreign_pattern=re.compile('[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uff66-\uff9f\u3001\u3002]|[A-Za-z]+(?:[._/-][A-Za-z0-9]+)*')
 for r in data:
  c=gold[r['case_id']];record=r['record'];group=member[f"{r['repeat']}:{r['case_id']}"];review=reviews.get(group['id']);wrong=c['scored'] and not correct(r,gold)
  semfail=review and (review['explanation_judgment']!='통과' or review['language_judgment']!='허용 표현')
  if wrong or not good(r) or semfail:failures.append({'repeat':r['repeat'],'case_id':r['case_id'],'question':c['question'],'scored':c['scored'],'expected':c['expected_ids'],'actual':ids(r),'error_kind':record.get('error_kind'),'error':record.get('error'),'id_wrong':bool(wrong),'semantic_review':review,'request_id':record.get('request_id')})
  scored_rows.append({'repeat':r['repeat'],'case_id':r['case_id'],'suite':r['suite'],'question':c['question'],'expected':json.dumps(c['expected_ids'],ensure_ascii=False),'actual':json.dumps(ids(r)),'scored':c['scored'],'exact_set':correct(r,gold) if c['scored'] else None,'ordered_exact':good(r) and ids(r)==c['expected_ids'] if c['scored'] else None,'valid':good(r),'duplicate_ids':len(ids(r))-len(set(ids(r))),'error_kind':record.get('error_kind'),'backend_seconds':record.get('elapsed_seconds'),'model_http_seconds':record.get('model_http_seconds'),'client_wrapper_seconds':r['client_http_seconds'],'input_tokens':record.get('input_tokens'),'output_tokens':record.get('output_tokens'),'reasoning_tokens':record.get('reasoning_tokens'),'review_group':group['id'],'language_review':review['language_judgment'] if review else '미검토','explanation_review':review['explanation_judgment'] if review else '미검토','id_reply_mismatch':review['id_reply_mismatch'] if review else None,'review_reason':review['reason'] if review else None})
  a=generated(record);texts=[]
  for key in ['reply','invalid_raw']:
   if a.get(key):texts.append((key,a[key]))
  for i,part in enumerate(a.get('requests',[]) if isinstance(a.get('requests',[]),list) else []):
   if isinstance(part,dict):
    for key in ['interpreted_request','reason']:
     if part.get(key):texts.append((f'requests[{i}].{key}',part[key]))
    for key in ['target_expressions','conditions']:
     for j,text in enumerate(part.get(key,[])):texts.append((f'requests[{i}].{key}[{j}]',text))
    if part.get('time_expression'):texts.append((f'requests[{i}].time_expression',part['time_expression']))
    for j,src in enumerate(part.get('context_used',[])):
     if isinstance(src,dict):texts.append((f'requests[{i}].context_used[{j}].quote',src.get('quote','')))
  for field,text in texts:
   candidates=[{'text':m.group(),'before':text[max(0,m.start()-35):m.start()],'after':text[m.end():m.end()+35]} for m in foreign_pattern.finditer(text)]
   decision='미검토' if not review else '실제 혼입' if field in review.get('language_actual_fields',[]) else '판정 보류' if field in review.get('language_pending_fields',[]) else review['language_judgment'] if review['language_judgment']=='판정 보류' else '허용 표현'
   language_rows.append({'repeat':r['repeat'],'case_id':r['case_id'],'field':field,'raw_text':text,'automatic_candidates':json.dumps(candidates,ensure_ascii=False),'candidate_count':len(candidates),'review_group':group['id'],'review_decision':decision,'review_reason':review['language_reason'] if review else None,'source':'검증실패 원시출력' if not good(r) else '정상 모델출력'})
 if scored_rows:
  with (OUT/'scored.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(scored_rows[0]));w.writeheader();w.writerows(scored_rows)
 if language_rows:
  with (OUT/'language_review.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(language_rows[0]));w.writeheader();w.writerows(language_rows)
 tokens=[(r['record'].get('output_tokens'),r['record'].get('model_http_seconds')) for r in data if isinstance(r['record'].get('output_tokens'),int) and isinstance(r['record'].get('model_http_seconds'),(int,float))]
 token_latency={'n':len(tokens),'pearson_r':corr([v[0] for v in tokens],[v[1] for v in tokens]),'note':'서로다른문항/문맥의 관측 상관이며 인과효과나 고정 생성속도 아님'}
 summary={'complete_runs':len(data)==414,'semantic_complete':len(reviews)==len(groups),'requests':len(data),'unique_question_turns_observed':len(seq),'planned_unique_question_turns':138,'groups':by_suite,'per_intent':per_intent,'special':special,'inside_wrong_rejection':{'n':len(inside),'out_of_scope':sum('out_of_scope' in ids(r) for r in inside),'clarify':sum('clarify' in ids(r) for r in inside)},'compound':compound,'dialogue':dialogue,'consistency':consistency,'per_repeat':{str(rep):metric([r for r in data if r['repeat']==rep],False) for rep in [1,2,3]},'token_latency':token_latency,'known_generation_calls':sum(r['record'].get('llm_call_count') or 0 for r in data),'unknown_call_count_runs':sum(r['record'].get('llm_call_count') is None for r in data),'single_call_invariant':all(r['record'].get('llm_call_count')==1 for r in data),'tools_absent':all('tools' not in r['record'].get('request',{}) for r in data),'user_questions_only_current_role':all((r['record'].get('request',{}).get('messages') or [{}])[-1].get('content')==gold[r['case_id']]['question'] for r in data),'all_app_hashes_unchanged':True,'review_groups':len(groups),'review_groups_completed':len(reviews),'language_field_rows':len(language_rows),'review_method':'모든서로다른 생성·원문맥락 묶음을 현재 AI가 읽고판정. turn_id만 사용자원문으로대응시켜 동일출력그룹화. raw원문불변. 실제독립/사람블라인드검토아님.'}
 dump(OUT/'summary.json',summary);dump(OUT/'failures.json',failures);dump(OUT/'policy_review_cases.json',[c for c in gold.values() if not c['scored']]);dump(OUT/'dialogue_metrics.json',dialogue)
 print(json.dumps({'n':len(data),'groups':len(groups),'reviewed':len(reviews),'accuracy':{k:ratio(v.get('correct',0),v['n']) for k,v in by_suite.items() if k not in ['policy_review','all_requests']},'errors':by_suite['all_requests']['errors']},ensure_ascii=False))
if __name__=='__main__':main()
