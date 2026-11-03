"""v2-only single-engine evaluation. One generation per question, no retries or repair."""
import argparse,csv,fcntl,hashlib,json,os,random,subprocess,sys,time
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend import classifier,config,llm_client
from evaluation.run_classification import state,inspect,fingerprint,idle_vllm,no_foreign_gpu
from evaluation.gpu_safety import exclusive
from evaluation.score import percentile,ratio
SUITES=['single','boundary','compound','adversarial','review']

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def append(p,v):
 with p.open('a') as f:f.write(json.dumps(v,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
def readrows(p):return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
def timed(rows,key):
 values=[r['record'][key] for r in rows if r['record'].get(key) is not None]
 return {'n':len(values),'p50':percentile(values,.5),'p95':percentile(values,.95)}
def valid(r):return r['record']['status']!='classification_error'
def correct(r):return valid(r) and set(r['record']['intents'])==set(r['case']['expected_intents'])
def metric(rows):
 return {'n':len(rows),'correct':sum(correct(r) for r in rows),'valid':sum(valid(r) for r in rows),'errors':sum(not valid(r) for r in rows),'truncated':sum(r['record'].get('error_kind')=='truncated' for r in rows),'ordered_correct':sum(correct(r) and r['record']['intents']==r['case']['expected_intents'] for r in rows),'backend':timed(rows,'elapsed_seconds'),'http':timed(rows,'model_http_seconds'),'input_tokens':sum(r['record'].get('input_tokens') or 0 for r in rows),'output_tokens':sum(r['record'].get('output_tokens') or 0 for r in rows),'reasoning_tokens':None if all(r['record'].get('reasoning_tokens') is None for r in rows) else sum(r['record'].get('reasoning_tokens') or 0 for r in rows)}

def score(out):
 manifest=json.loads((out/'manifest.json').read_text());rows=readrows(out/'runs.jsonl');scored=[r for r in rows if r['case']['scored']];reviews=[r for r in rows if not r['case']['scored']]
 if any(sha(ROOT/p)!=h for p,h in manifest['hashes'].items()):raise RuntimeError('고정 입력 해시 변경')
 groups={s:metric([r for r in scored if r['case']['suite']==s]) for s in SUITES if s!='review'};groups['scored_overall']=metric(scored)
 allmetrics={'requests':len(rows),'valid':sum(valid(r) for r in rows),'errors':sum(not valid(r) for r in rows),'truncated':sum(r['record'].get('error_kind')=='truncated' for r in rows),'model_calls':sum(r['record']['llm_call_count'] for r in rows),'backend':timed(rows,'elapsed_seconds'),'http':timed(rows,'model_http_seconds')}
 special={}
 for label in ['clarify','out_of_scope']:
  truth=[r for r in scored if label in r['case']['expected_intents']];pred=[r for r in scored if valid(r) and label in r['record']['intents']];tp=sum(label in r['case']['expected_intents'] for r in pred)
  special[label]={'true_positive':tp,'predicted':len(pred),'expected':len(truth),'precision':ratio(tp,len(pred)),'recall':ratio(tp,len(truth))}
 inside=[r for r in scored if 'out_of_scope' not in r['case']['expected_intents'] and 'clarify' not in r['case']['expected_intents']]
 compound=[r for r in scored if r['case']['suite']=='compound'];truth_count=sum(len(r['case']['expected_intents']) for r in compound)
 missed=sum(len(set(r['case']['expected_intents'])-set(r['record']['intents'])) for r in compound);extras=sum(len(set(r['record']['intents'])-set(r['case']['expected_intents'])) for r in compound);pred_count=sum(len(r['record']['intents']) for r in compound)
 byid=defaultdict(list)
 for r in rows:byid[r['case']['id']].append(r)
 consistency={s:{'same':sum(len(v)==3 and len({(r['record']['status'],tuple(r['record']['intents'])) for r in v})==1 for v in byid.values() if v[0]['case']['suite']==s),'n':sum(len(v)==3 for v in byid.values() if v[0]['case']['suite']==s)} for s in SUITES}
 invariants={'exactly_one_generation':all(r['record']['llm_call_count']==1 for r in rows),'only_system_user':all([m['role'] for m in r['record']['request']['messages']]==['system','user'] for r in rows),'user_question_only':all(r['record']['request']['messages'][1]['content']==r['case']['question'] for r in rows),'no_tools':all('tools' not in r['record']['request'] for r in rows),'fixed_system':len({r['record']['request']['messages'][0]['content'] for r in rows})<=1,'no_question_list_in_system':all(c['question'] not in classifier.messages('',classifier.load_taxonomy())[0]['content'] for c in manifest['cases']),'all_hashes_unchanged':True,'no_ollama_generations':all(r['record']['backend']=='vllm_qwen' for r in rows)}
 per_intent={i:metric([r for r in scored if r['case']['suite']=='single' and r['case']['expected_intents']==[i]]) for i in config.INTENT_IDS}
 failures=[{'repeat':r['repeat'],**r['case'],'actual':r['record']['intents'],'error_kind':r['record'].get('error_kind'),'content':r['record'].get('content')} for r in scored if not correct(r)]
 labels=[*config.INTENT_IDS,'classification_error','multi']
 matrix={x:{y:0 for y in labels} for x in config.INTENT_IDS}
 for r in scored:
  if len(r['case']['expected_intents'])!=1:continue
  actual=r['record']['intents'];target='classification_error' if not valid(r) else actual[0] if len(actual)==1 else 'multi';matrix[r['case']['expected_intents'][0]][target]+=1
 with (out/'confusion_matrix.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['expected/actual',*labels]);w.writerows([i,*[matrix[i][j] for j in labels]] for i in config.INTENT_IDS)
 with (out/'scored.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['case_id','repeat','suite','question','expected','actual','scored','exact_set','ordered_exact','error','backend_seconds','http_seconds','input_tokens','output_tokens'])
  for r in rows:
   c=r['case'];v=r['record'];w.writerow([c['id'],r['repeat'],c['suite'],c['question'],json.dumps(c['expected_intents']),json.dumps(v['intents']),c['scored'],correct(r) if c['scored'] else '',v['intents']==c['expected_intents'] if c['scored'] and valid(r) else False if c['scored'] else '',v.get('error_kind'),v['elapsed_seconds'],v.get('model_http_seconds'),v.get('input_tokens'),v.get('output_tokens')])
 reportrows=[]
 for suite,m in groups.items():
  sec=lambda x:f'{x:.3f}s' if x is not None else '미제공'
  reportrows.append({'suite':suite,'accuracy':ratio(m['correct'],m['n']),'valid':ratio(m['valid'],m['n']),'p50':sec(m['backend']['p50']),'p95':sec(m['backend']['p95']),'http_p50':sec(m['http']['p50']),'http_p95':sec(m['http']['p95'])})
 summary={'complete':len(rows)==len(manifest['cases'])*3,'versions':classifier.versions(),'rows':reportrows,'all_requests':allmetrics,'groups':groups,'per_intent':per_intent,'special':special,'inside_wrong_rejection':{'outside':sum('out_of_scope' in r['record']['intents'] for r in inside),'clarify':sum('clarify' in r['record']['intents'] for r in inside),'n':len(inside)},'compound':{'missing_ids':missed,'expected_ids':truth_count,'missing_rate':ratio(missed,truth_count),'extra_ids':extras,'predicted_ids':pred_count,'extra_rate':ratio(extras,pred_count),'runs_with_missing':sum(bool(set(r['case']['expected_intents'])-set(r['record']['intents'])) for r in compound),'runs_with_extra':sum(bool(set(r['record']['intents'])-set(r['case']['expected_intents'])) for r in compound),'n':len(compound)},'consistency':consistency,'invariants':invariants,'review_requests_unscored':len(reviews)}
 dump(out/'summary.json',summary);dump(out/'failures.json',failures);dump(out/'boundary_review_unscored.json',reviews)
 with (out/'metrics.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(reportrows[0]));w.writeheader();w.writerows(reportrows)
 lines=['# 의도 분류체계 2차정리 · 검토안 실측 평가','',f"실험 {out.name} · {classifier.versions()}",'','본 결과는 2차 정의만 사용한 검토안 분류 성능이다. 1차 점수를 기준선이나 개선율로 사용하지 않았다. 실제 업무 답변·검색·도구·계획·예측은 실행하지 않았다.','',f"고유 {len(manifest['cases'])}문항 ×3회 = {len(rows)}요청. 점수 대상 {len(scored)}건, 정책 미확정 검토 {len(reviews)}건은 정확도에서 제외. 준비 요청은 warmups.jsonl에 따로 저장하고 점수/시간에서 제외했다.",'','|묶음|정답(오류 포함)|유효 형식|전체 p50|전체 p95|HTTP p50|HTTP p95|','|---|---:|---:|---:|---:|---:|---:|']
 for row in reportrows:lines.append('|'+ '|'.join(str(row[k]) for k in row)+'|')
 lines+=['','## 설정·측정 범위','',f"모델: {manifest['model_metadata']['models']}。 vLLM version={manifest['model_metadata']['version']}。 요청 temperature=0, seed=20261012, enable_thinking=false, max_tokens=256。 기존 이미지/서버/가중치/컨텍스트 설정은 변경하지 않았다.",f"출력 수용 사전 검사: {manifest['output_budget_check']}。 15개 ID를 모두 출력하는 정상 JSON의 실제 모델 토크나이저 기준 길이를 확인했다. 최댓값 추가/출력 잘림 재시도는 없다.",'백엔드 시간은 함수 진입→가용성 확인·생성 HTTP·검증 종료이며 로그 저장 시간은 제외한다. HTTP 시간은 생성 POST→수신이다. 워밍업/캐시 초기화 비용은 포함하지 않는다. 질문 순서는 반복별 고정 난수 셔플로 기록했으며 prefix cache는 초기화하지 않았다. 소규모 합성셋 p95를 운영 보장으로 해석하지 않는다. vLLM 미제공 적재·입력 처리·생성 시간과 별도 추론 토큰 수는 추정하지 않는다.',f"전체 요청 계수/형식: {allmetrics}",f"요청 불변 검사: {invariants}",'','## 분류별 단일 의도 성적','', '|ID|정답|오류|','|---|---:|---:|']
 for i,m in per_intent.items():lines.append(f"|{i}|{ratio(m['correct'],m['n'])}|{m['errors']}|")
 lines+=['','## 복합·범위·반복','',json.dumps(summary['compound'],ensure_ascii=False),json.dumps(special,ensure_ascii=False),f"범위 안 오거부: {summary['inside_wrong_rejection']}",f"반복 일치(순서 포함, 고유 질문별): {consistency}",'','## 오분류와 정의 경계','']
 failby=defaultdict(list)
 for f in failures:failby[f['id']].append(f)
 for iid,fs in failby.items():
  f=fs[0];lines.append(f"- {iid} ({len(fs)}/3회 오답): {f['question']} 기대 {f['expected_intents']} → 관찰 {[x['actual'] for x in fs]}. 사전 기준: {f['rationale']}")
 lines+=['','## 미확정 정책과 보완 제안','', '- 독립적인 후속 분석과 목적 달성용 보조 조회의 구분은 문장에 따라 달라질 수 있다. 이번 정답 기준은 실행 전에 고정했고 사후 허용 답을 추가하지 않았다.', '- whatif.simulate / proposal.apply / dt.update: 가상 조건 계산·기존 제안 적용 요청·트윈 구조/정보 반영을 구별하는 추가 합의가 필요하다. 복합 동사가 있는 미확정 사례는 별도 검토했다.', '- clarify / out_of_scope가 부분 요청에만 해당할 때 다른 ID와 병기할지 전체 요청 상태로 볼지 미확정이다. 관련 문항은 일반 성적에서 제외했다.', '- 문서 적합성·예측 적용 가능성을 data.analyze로 분류하더라도 현재 도구/문서 지원이나 실제 결과 생성 기능을 보장하지 않는다.', '- 15개 ID·명칭·범위·복합 정책을 이 평가로 확정하지 않는다. 실제 현장 정답 라벨 검토와 보지 않은 질문에서의 추가 검증이 필요하다.','', '## 별도 경계 검토 (정답 미확정·점수 제외)','']
 for r in reviews:lines.append(f"- {r['case']['id']} 반복{r['repeat']}: {r['case']['question']} → {r['record']['intents']}。 쟁점: {r['case']['rationale']}")
 lines+=['','## 파일·재실행','',f'`python -m evaluation.run_second_review --experiment <새ID>`。 현재 실험 재개는 동일 ID로 가능하며 이미 기록된 성공/실패를 재호출하지 않는다. 모든 입력 해시를 검사한다. 원본은 runs.jsonl, 준비 요청 warmups.jsonl, questions_gold.json, manifest.json, scored.csv, failures.json, confusion_matrix.csv이다. 사용자 화면 확인은 ui_checks.json 및 스크린샷을 참조한다. 1차 파일·점수는 별도 보존했고 현재 목록/요청과 분리했다.']
 (out/'comparison_report.md').write_text('\n'.join(lines)+'\n');return summary

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--experiment',required=True);ap.add_argument('--score-only',action='store_true');args=ap.parse_args()
 if not args.experiment.replace('-','').replace('_','').isalnum():raise ValueError('invalid experiment ID')
 out=ROOT/'evaluation/results'/args.experiment
 if args.score_only:print(json.dumps(score(out)['rows'],ensure_ascii=False));return
 if not config.TAXONOMY_VERSION.startswith('food-manufacturing-second-review'):raise RuntimeError('v2 only')
 out.mkdir(parents=True,exist_ok=True)
 lock=(ROOT/'data/runtime/evaluation.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);lock.seek(0);lock.truncate();lock.write(str(os.getpid()));lock.flush();os.environ['LAM_EVALUATOR_PID']=str(os.getpid())
 try:
  target=config.targets()['vllm_qwen'];availability=llm_client.probe(target)
  if not availability['can_run']:raise RuntimeError(str(availability))
  exclusive('vllm_qwen',state());no_foreign_gpu();idle_vllm()
  cases=[]
  for suite in SUITES:cases+=json.loads((ROOT/f'evaluation/datasets/second_review/{suite}.json').read_text())
  names=['backend/classifier.py','backend/config.py','backend/llm_client.py','backend/main.py','backend/schemas.py','frontend/app.js','frontend/index.html','frontend/app.css','evaluation/run_second_review.py','evaluation/gpu_safety.py','evaluation/run_classification.py','evaluation/score.py',config.TAXONOMY_FILE,config.PROMPT_FILE,'taxonomy/second_review_source.txt','evaluation/datasets/second_review/policy.md']+[f'evaluation/datasets/second_review/{s}.json' for s in SUITES]
  hashes={f:sha(ROOT/f) for f in names};mp=out/'manifest.json'
  if mp.exists():
   manifest=json.loads(mp.read_text())
   if manifest['hashes']!=hashes:raise RuntimeError('frozen inputs changed')
  else:
   md=Path('/home/yt/vllm/models/Qwen3.8-27B-FP8');model_files=[p for p in md.iterdir() if p.is_file()];container=inspect('vllm-qwen');budget=json.loads((ROOT/'data/runtime/v2-work/output_budget.json').read_text())
   manifest={'experiment':args.experiment,'created_at':datetime.now(timezone.utc).isoformat(),'versions':classifier.versions(),'hashes':hashes,'cases':cases,'repeat_orders':{str(rep):random.Random(20261012+rep).sample([c['id'] for c in cases],len(cases)) for rep in [1,2,3]},'repetitions':3,'model_metadata':{'models':llm_client.http(target,'/models'),'version':json.loads(subprocess.check_output(['curl','-sf',target.url.removesuffix('/v1')+'/version'],text=True)),'image':container['Config']['Image'],'image_id':container['Image'],'container_config_sha256':fingerprint(container),'model_file_stat':{p.name:{'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in model_files},'config_hashes':{p.name:sha(p) for p in model_files if p.suffix=='.json' or p.suffix=='.jinja'},'revision':(md/'.cache/huggingface/download/config.json.metadata').read_text()},'output_budget_check':budget,'requested_payload_without_question':llm_client.payload(target,[])[1],'initial_state':state(),'first_review_excluded':'No first-review definitions/questions/gold/results are read as model or scoring inputs. Their files remain unmodified.'}
   dump(mp,manifest);dump(out/'questions_gold.json',cases);dump(out/'definitions.json',classifier.load_taxonomy());(out/'classification_prompt.md').write_text((ROOT/config.PROMPT_FILE).read_text());(out/'grading_policy.md').write_text((ROOT/'evaluation/datasets/second_review/policy.md').read_text())
  def check():
   if any(sha(ROOT/p)!=h for p,h in hashes.items()):raise RuntimeError('input hashes changed')
   if fingerprint(inspect('vllm-qwen'))!=manifest['model_metadata']['container_config_sha256']:raise RuntimeError('server configuration changed')
   no_foreign_gpu();exclusive('vllm_qwen',state())
  done={(r['repeat'],r['case']['id']) for r in readrows(out/'runs.jsonl')};warmdone={r['repeat'] for r in readrows(out/'warmups.jsonl')};byid={c['id']:c for c in cases}
  for rep in [1,2,3]:
   check()
   if rep not in warmdone:
    warm=classifier.classify('vllm_qwen','지난 주말 저장된 세척 운전 원기록만 조회해줘.')
    append(out/'warmups.jsonl',{'repeat':rep,'record':warm,'state':state()})
    if warm['status']=='classification_error':raise RuntimeError('preparation failed')
   for iid in manifest['repeat_orders'][str(rep)]:
    if (rep,iid) in done:continue
    check();case=byid[iid];record=classifier.classify('vllm_qwen',case['question']);after=state();exclusive('vllm_qwen',after)
    append(out/'runs.jsonl',{'repeat':rep,'case':case,'record':record,'gpu_after':after,'warm':True});done.add((rep,iid))
    print(rep,iid,record['intents'],round(record['elapsed_seconds'],3),record.get('error_kind'),flush=True)
   score(out)
  check();dump(out/'completion.json',{'at':datetime.now(timezone.utc).isoformat(),'hashes_unchanged':True,'final_state':state(),'container_config_unchanged':True});summary=score(out);print('COMPLETE',len(done),json.dumps(summary['rows'],ensure_ascii=False),flush=True)
 finally:
  lock.seek(0);lock.truncate();lock.flush();fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
if __name__=='__main__':main()
