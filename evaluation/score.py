"""No semantic guessing: fixed-label scoring and separately labeled timing populations."""
import argparse,csv,json,math,statistics,hashlib
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def percentile(values,p):
 if not values:return None
 v=sorted(values);x=(len(v)-1)*p;i=int(x);return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i)
def ratio(n,d):return f'{n}/{d} ({n/d*100:.2f}%)' if d else '미측정'
def correct(r):return r['record']['status']==r['case']['expected_status'] and r['record']['intent_id']==r['case']['expected_id']
def metrics(rows):
 valid=[r for r in rows if r['record']['status']!='classification_error'];normal=[r for r in rows if not r.get('performance_excluded')];success=[r for r in normal if r['record']['status']!='classification_error'];fail=[r for r in normal if r['record']['status']=='classification_error']
 def timing(rs,key):return {'n':len([r for r in rs if r['record'].get(key) is not None]),'p50':percentile([r['record'][key] for r in rs if r['record'].get(key) is not None],.5),'p95':percentile([r['record'][key] for r in rs if r['record'].get(key) is not None],.95)}
 return {'n':len(rows),'correct':sum(correct(r) for r in rows),'valid':len(valid),'errors':len(rows)-len(valid),'truncated':sum(r['record'].get('error_kind')=='truncated' for r in rows),'timeouts':sum(r['record'].get('error_kind')=='timeout' for r in rows),'api_errors':sum(r['record'].get('error_kind') in ('api_error','connection_error') for r in rows),'model_calls':sum(r['record']['llm_call_count'] for r in rows),'preflight_blocked':sum(r['record']['llm_call_count']==0 for r in rows),'performance_excluded':len(rows)-len(normal),'all_time':timing(normal,'elapsed_seconds'),'http_time':timing(normal,'model_http_seconds'),'valid_time':timing(success,'elapsed_seconds'),'failed_time':timing(fail,'elapsed_seconds'),'correct_time':timing([r for r in normal if correct(r)],'elapsed_seconds'),'incorrect_time':timing([r for r in normal if not correct(r)],'elapsed_seconds'),'input_tokens':sum(r['record'].get('input_tokens') or 0 for r in rows),'output_tokens':sum(r['record'].get('output_tokens') or 0 for r in rows),'reasoning_tokens':None if all(r['record'].get('reasoning_tokens') is None for r in rows) else sum(r['record'].get('reasoning_tokens') or 0 for r in rows),'gpu_peak_mib':{i:max([r['gpu_peak_mib'][i] for r in rows if i in r.get('gpu_peak_mib',{})],default=None) for i in ['0','1']}}

def main():
 p=argparse.ArgumentParser();p.add_argument('--experiment',required=True);args=p.parse_args();out=ROOT/'evaluation/results'/args.experiment;manifest=json.loads((out/'manifest.json').read_text())
 if any(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()!=h for f,h in manifest['hashes'].items()):raise RuntimeError('frozen inputs changed')
 exclusions=json.loads((out/'measurement_exclusions.json').read_text()) if (out/'measurement_exclusions.json').exists() else []
 summary={'complete':False,'paused':(out/'paused.json').exists(),'rows':[],'backends':{}};allfail=[];labels=[str(i) for i in range(1,67)]+['out_of_scope','unclear','multiple_intents','classification_error']
 for key in manifest['requested_settings']:
  path=out/key/'runs.jsonl';rows=[json.loads(l) for l in path.read_text().splitlines()] if path.exists() else [];
  for r in rows:
   for exclusion in exclusions:
    if key==exclusion['backend'] and r['repeat']==exclusion['repeat']:r['performance_excluded']=True;r['exclusion_reason']=exclusion['reason']
  groups={suite:metrics([r for r in rows if r['case']['suite']==suite]) for suite in ['basic','edge']};groups['overall']=metrics(rows)
  by_id=defaultdict(list)
  for r in rows:by_id[r['case']['id']].append(r)
  full=[v for v in by_id.values() if len(v)==3];same=sum(len({(r['record']['status'],r['record']['intent_id']) for r in v})==1 for v in full)
  groups['consistency']={'same':same,'unique_cases_three_repeats':len(full),'all_three_correct':sum(all(correct(r) for r in v) for v in full),'at_least_one_correct':sum(any(correct(r) for r in v) for v in full)}
  outside=[r for r in rows if r['case']['expected_status']=='out_of_scope'];inside=[r for r in rows if r['case']['expected_status']=='classified'];groups['scope']={'outside_rejected':sum(r['record']['status']=='out_of_scope' for r in outside),'outside_total':len(outside),'inside_wrong_outside':sum(r['record']['status']=='out_of_scope' for r in inside),'inside_total':len(inside),'inside_nonclassified':sum(r['record']['status']!='classified' for r in inside)}
  groups['special']={s:{'correct':sum(correct(r) for r in rows if r['case']['expected_status']==s),'total':sum(r['case']['expected_status']==s for r in rows)} for s in ['unclear','multiple_intents']}
  groups['per_repeat']={str(rep):metrics([r for r in rows if r['repeat']==rep]) for rep in [1,2,3]}
  groups['invariants']={'single_call':all(r['record']['llm_call_count']==1 for r in rows),'tools_absent':all('tools' not in r['record'].get('request',{}) for r in rows),'two_roles':all([m['role'] for m in r['record'].get('request',{}).get('messages',[])]==['system','user'] for r in rows),'only_user_question':all((r['record'].get('request',{}).get('messages') or [{},{}])[1].get('content')==r['case']['question'] for r in rows)}
  summary['backends'][key]=groups;g=groups['overall'];s=groups['basic'];e=groups['edge']
  sec=lambda x:f'{x:.3f}s' if x is not None else '미측정'
  summary['rows'].append({'backend':key,'basic':ratio(s['correct'],s['n']),'edge':ratio(e['correct'],e['n']),'errors':f"{g['errors']}/{g['n']} · 잘림 {g['truncated']}",'p50':sec(g['all_time']['p50']),'p95':sec(g['all_time']['p95']),'consistency':ratio(same,len(full)),'gpu':' / '.join(f'GPU{i} {v}MiB' for i,v in g['gpu_peak_mib'].items() if v is not None),'progress':f"{g['n']}/258"})
  matrix={x:{y:0 for y in labels} for x in labels};fail=[]
  with (out/key/'scored.csv').open('w',newline='') as f:
   w=csv.writer(f);w.writerow(['repeat','case_id','suite','question','expected_status','expected_id','actual_status','actual_id','correct','error_kind','all_seconds','http_seconds','excluded'])
   for r in rows:
    a=r['case'];b=r['record'];w.writerow([r['repeat'],a['id'],a['suite'],a['question'],a['expected_status'],a['expected_id'],b['status'],b['intent_id'],correct(r),b.get('error_kind'),b['elapsed_seconds'],b.get('model_http_seconds'),r.get('performance_excluded')]);x=str(a['expected_id']) if a['expected_status']=='classified' else a['expected_status'];y=str(b['intent_id']) if b['status']=='classified' else b['status'];matrix[x][y]+=1
    if not correct(r):fail.append({'backend':key,'repeat':r['repeat'],'case_id':a['id'],'question':a['question'],'expected_status':a['expected_status'],'expected_id':a['expected_id'],'actual_status':b['status'],'actual_id':b['intent_id'],'error_kind':b.get('error_kind'),'finish_reason':b.get('finish_reason')})
  with (out/key/'confusion_matrix.csv').open('w',newline='') as f:w=csv.writer(f);w.writerow(['expected/actual',*labels]);w.writerows([[x,*[matrix[x][y] for y in labels]] for x in labels])
  (out/key/'metrics.json').write_text(json.dumps(groups,ensure_ascii=False,indent=2));(out/key/'failures.json').write_text(json.dumps(fail,ensure_ascii=False,indent=2));allfail+=fail
 summary['complete']=all(b['overall']['n']==258 for b in summary['backends'].values()) and (out/'restoration.json').exists()
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
 with (out/'comparison.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(summary['rows'][0]));w.writeheader();w.writerows(summary['rows'])
 (out/'failures.json').write_text(json.dumps(allfail,ensure_ascii=False,indent=2))
 lines=['# 식품 제조 의도 분류 — 새 프로젝트 실측 비교','',f"실험: {args.experiment} · 상태: {'사용자 요청으로 보류·부분 결과' if summary['paused'] else '완료' if summary['complete'] else '진행 중/부분 결과'}",'', '과거 프로젝트를 참조·복구하지 않고 새 분류기에서 실행했다. 과거 수치를 새 실측값으로 사용하지 않았다. 기본 66개와 예외20개가 고유 86문항이며, 구성별 각3회(258요청), 총774요청을 계획했다. 변형16개는 사전 작성·해시 고정했으나 실행하지 않았다.', '',
 '## 구성별 결과','', '| 구성 | 기본 정답률 | 예외 판정 | 오류/잘림 | warm p50 | warm p95 | 반복 일치 | GPU 메모리 | 진행 |','|---|---:|---:|---:|---:|---:|---:|---|---|']
 for r in summary['rows']:lines.append('| '+' | '.join(str(r[k]) for k in ['backend','basic','edge','errors','p50','p95','consistency','gpu','progress'])+' |')
 lines+=['','오류도 오답에 포함했다. 위 warm 시간은 성공·실패를 모두 포함하되 CPU 오프로딩 실행은 정상 성능 집계에서 제외했다. 빠른 실패를 성능 개선으로 해석하지 않는다. 유효 JSON과 정답률은 별개다. 미실행은 정확도 0%가 아니라 미측정이다.','', '## 비교 조건과 해석','',
 '- 순서: 반복1 vLLM Qwen→Ollama Qwen→Ollama GPT-OSS; 반복2 Ollama Qwen→Ollama GPT-OSS→vLLM Qwen; 반복3 Ollama GPT-OSS→vLLM Qwen→Ollama Qwen. 각 블록에 동일 질문 순서와 별도 준비 요청 한 번을 적용했다. 준비 요청·모델 전환은 warm 점수/시간에서 제외하고 warmups.jsonl/transitions.jsonl에 보존한다.',
 '- vLLM Qwen은 FP8 체크포인트, Ollama Qwen은 API상 Q4_K_M GGUF로 가중치 출처·양자화·템플릿이 다르다. 순수 엔진 비교가 아니라 실제 배포 구성 비교다. GPT-OSS는 MXFP4 116.8B이며 Qwen과 크기/구조/think 설정이 다르다. 정확한 digest·revision 가능한 정보·템플릿 해시는 manifest.json, warmups.jsonl의 availability.capabilities, server_metadata.jsonl에 있다.',
 '- 요청 설정: temperature=0, seed=20261012, 출력 한도256. vLLM은 enable_thinking=false, Ollama Qwen은 think=false, GPT-OSS는 /api/show가 지원하는 최저 think=low. 요청값과 실제 reasoning 반환 여부/토큰은 원본에 따로 기록한다. 지원 옵션 목록이 실제 적용을 증명하는 것으로 취급하지 않는다.',
 '- GPT-OSS의 reasoning은 API가 별도로 반환하면 content와 분리 저장한다. 256토큰 한도에서 JSON을 완성하지 못하면 잘림 실패다. 한도를 늘리거나 다시 요청하지 않았다. token count 의미가 엔진별로 완전히 동일하다고 가정하지 않는다. 미제공 reasoning 토큰 수는 null이다.',
 '- 모델이 이미 준비된 warm 요청의 성능이다. prefix cache를 초기화하지 않았으며 엔진별 캐시 구현이 다르다. Cold 분류 비교는 동등한 시작 상태를 통제하지 못해 생략했다. vLLM 시작→API 준비 시간과 Ollama 준비 요청의 load_duration은 별도 전환/준비 기록이며 warm과 합치지 않는다.',
 '- backend 전체 시간은 분류 함수 진입→가용성 확인·HTTP·검증 종료, HTTP 시간은 실제 생성 POST부터 응답 수신까지다. 파일 저장은 측정 뒤 수행한다. GPU 메모리는 약0.25초 표본 최대치이며 연속 계측에 의한 절대 최대 보장이 아니다. 각 요청 전0.5초/종료 후0.1초의 인접 표본을 포함할 수 있어 요청별 독점 메모리 증가분으로 해석하지 않는다.',
 '- 기본 예문의 점수를 현장 질문의 일반화 성능이나 실제 식품 제조 답변·예측 정확도로 표현하지 않는다. 짧은 JSON 분류 지연을 전체 LAM 처리시간과 직접 비교하지 않는다.', '', '## 세부 지표']
 for key,g in summary['backends'].items():
  o=g['overall'];lines+=['',f'### {key}', '', f"실제 생성 API 호출 {o['model_calls']}회, 생성 전 사전 차단 {o['preflight_blocked']}건. 유효 출력 {ratio(o['valid'],o['n'])}; 오류 {o['errors']}, API 오류 {o['api_errors']}, 타임아웃 {o['timeouts']}, 잘림 {o['truncated']}; 성능 제외 {o['performance_excluded']}.",f"전체 요청 시간 {o['all_time']}; HTTP 시간 {o['http_time']}; 유효 출력 시간 {o['valid_time']}; 오류 시간 {o['failed_time']}; 정답 시간 {o['correct_time']}; 오답 시간 {o['incorrect_time']}.",f"범위 판정 {g['scope']}; 모호/복합 {g['special']}; 반복 일치(status/id 기준) {g['consistency']}.",f"입력 토큰 {o['input_tokens']}, 출력 토큰 {o['output_tokens']}, 별도 reasoning 토큰 {o['reasoning_tokens']}. 원본 Ollama load/prompt/eval ns는 runs.jsonl에 있으며 vLLM 미제공 값은 null이다.",f"요청 계약 검사 {g['invariants']}."]
 lines+=['', '## 실패 사례','']
 for f in allfail[:70]:lines.append(f"- {f['backend']} 반복{f['repeat']} {f['case_id']} 기대 {f['expected_id'] or f['expected_status']} → 실제 {f['actual_id'] or f['actual_status']} ({f['error_kind'] or '정상 형식, 번호/상태 불일치'}): {f['question']}")
 if len(allfail)>70:lines.append(f'전체 실패 {len(allfail)}건은 failures.json에 모두 보존했다. 위에는 앞70건만 표시한다.')
 lines+=['','54번은 세척 절차와 이력을 한 정의가 포괄하는지, 58번은 보관 환경 관리와 기준 이탈 확인의 범위가 겹치는 경계다. 사용자 표의 정답을 고정해 엄격히 채점했고, 결과를 보고 허용 번호나 문장 예외를 추가하지 않았다. 복합 예외는 서로 독립적인 업무를 동등하게 요구한다는 사전 기준을 사용했다.','', '## 복원과 한계','']
 if exclusions:
  lines.append('이 실험에는 동시 적재/사전 차단 구간이 있어 measurement_exclusions.json대로 정상 성능 집계에서 제외했다. 원본은 수정하지 않았다. 제어기 중단/재개 기록이 있다면 resume_notes.jsonl에 보존했다.')
 else:
  lines.append('이 새 실험은 중지된 엔진을 해제한 뒤에만 다음 엔진을 시작하는 강화된 정책으로 실행했다. 이전 보류 실험의 원본이나 수치를 가져오지 않았다. 동시 적재 감지나 성능 제외 구간은 없었다. 각 전환의 gpu_release_confirmed와 transition_ready 및 문항별 safety_violation 기록을 확인할 수 있다.')
 if (out/'restoration.json').exists():
  restoration=json.loads((out/'restoration.json').read_text());lines.append('복원 결과: '+json.dumps(restoration['services'],ensure_ascii=False));lines.append('입력 해시 보존: '+str(restoration['hashes_unchanged']))
 else:lines.append('평가가 아직 진행 중이거나 복원 결과 미기록이다. 완료로 간주하지 않는다.')
 if summary['complete'] and not summary['paused']:
  candidates=[(key,g) for key,g in summary['backends'].items() if g['overall']['performance_excluded']==0 and g['overall']['n']==258]
  if candidates:
   candidates.sort(key=lambda x:(-(x[1]['basic']['correct']+x[1]['edge']['correct']),x[1]['overall']['errors'],x[1]['overall']['all_time']['p50'] or float('inf')));lines.append('현재 고정 분류 테스트에서의 잠정 권고: '+candidates[0][0]+'. 기대 일치 수·오류·warm 지연 순으로 비교했다. 의미적 정답 기준 자체와 현장 일반화 검증을 대체하지 않으며 모델 전환 운영 비용을 별도로 고려해야 한다.')
 else:lines.append('3회 반복을 완료하지 않았고 사용자가 남은 평가 보류를 요청했으므로 최종 추천을 하지 않는다. 보류 상태에서 자동 재개하지 않는다.')
 lines+=['','UI는 localhost:8080이며 조회는 모델 적재/해제나 컨테이너 조작을 수행하지 않는다. 현재 안전한 실행이 불가한 구성은 이유를 표시한다. 순차 평가 절차는 README에 있다. 원본 파일은 각 구성의 runs.jsonl, 준비 요청 warmups.jsonl, 전환 transitions.jsonl, manifest.json이다.']
 (out/'comparison_report.md').write_text('\n'.join(lines)+'\n');print(json.dumps(summary['rows'],ensure_ascii=False,indent=2))
if __name__=='__main__':main()
