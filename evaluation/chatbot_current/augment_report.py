"""Final display additions from existing measurements. No generation or rescoring."""
import csv,hashlib,json,re
from pathlib import Path
from collections import Counter,defaultdict
OUT=Path('/home/yt/lam/evaluation/results/chatbot-eval-20261029-01')
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def main():
 with (OUT/'language_review.csv').open() as f:lr=list(csv.DictReader(f))
 counts=Counter();fields=defaultdict(Counter)
 for row in lr:
  field=row['field'];base='reply' if field=='reply' else field.split('.',1)[-1].split('[')[0];fields[base]['rows']+=1;fields[base][row['review_decision']]+=1
  for c in json.loads(row['automatic_candidates']):
   text=c['text'];category='한자·가나·일본어문장부호' if re.search('[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uff66-\uff9f\u3001\u3002]',text) else '영문토큰';counts[category]+=1;fields[base]['automatic_candidate_tokens']+=1
 actual=[r for r in lr if r['review_decision']=='실제 혼입'];pending=[r for r in lr if r['review_decision']=='판정 보류'];assert not actual and not pending
 language={'reviewed_primary_responses':414,'actual_mixed_responses':0,'by_actual_language':{'중국어':0,'일본어':0,'요청하지않은영어설명':0,'기타':0},'automatic_candidate_tokens':dict(counts),'by_field':{k:dict(v) for k,v in fields.items()},'judgment_method':'Character/Latin-token candidates plus AI reading of every distinct full response/context. Tokens are not language verdicts. All raw response text preserved.','ui_responses_separate':4,'ui_actual_mixing':0};dump(OUT/'language_summary.json',language)
 interference=json.loads((OUT/'interference_audit.json').read_text());audit=json.loads((OUT/'final_audit.json').read_text());summary=json.loads((OUT/'summary.json').read_text());summary['measurement_condition']={'isolation_verified':False,**{k:interference[k] for k in ['unattributed_posts_primary_window','unattributed_posts_whole_window','primary_runs_with_unattributed_response_inside_request_window']},'latencies':'Warm model, shared-server observations. Unknown concurrent source prevents isolated benchmark interpretation. No sample corrections or retries.'};dump(OUT/'summary.json',summary)
 caution='**응답시간 조건 주의:** 실제모델은 warm 상태였지만 측정종료까지 서버접근로그의 생성POST431개 중 평가귀속419개 외 **12개가 추가로 기록**됐다. 앱대화기록에 추가분이 없어 출처는미확정이다. 본414건구간에는11개, 평가요청시간과겹치는응답로그는10개다. 아래시간은 공유서버에서의관측치이며 완전히격리된속도벤치마크로해석하지않는다. 원본/정답/시간을고치거나 영향을추정해보정하지않았다.\n\n'
 extra=['','## 언어별·출력필드별 최종 판정','', '|자동 검사 종류|후보 토큰 수|문맥 검토상 실제 혼입|','|---|---:|---:|',f"|한자·가나·일본어 문장부호|{counts['한자·가나·일본어문장부호']}|0|",f"|영문 토큰(의도ID·약어·태그 등 포함)|{counts['영문토큰']}|0|",'', '최종 실제 언어 혼입은 중국어0·일본어0·요청하지않은영어문장0·기타0으로, 이표본에서관찰되지않았다. 후보토큰은언어판정이아니다. 실제혼입/판정보류가없어 재현표현목록도없다. 라면물온도 문항3회는 knowledge.lookup으로관찰됐지만 정책미확정으로정확도에서는제외했고 언어검사는모두실시했다.','', '|필드 유형|검토한 필드 원문 수|자동 후보 토큰|실제 혼입|판정 보류|','|---|---:|---:|---:|---:|']
 for name,v in fields.items():extra.append(f"|{name}|{v['rows']}|{v['automatic_candidate_tokens']}|{v['실제 혼입']}|{v['판정 보류']}|")
 extra+=['','## 공유 서버 접근로그의 시간조건 제한','', '- 첫 감사433개는 감사시점까지의로그였고 평가종료후요청2개도포함했다. 이후 전체측정범위를 manifest생성→마지막UI응답완료로고정해431개로다시계수했다. 최초감사파일도 *_initial_unbounded.json으로보존했다.','- 평가귀속419개를 앱완료시각과접근로그종료시각으로대조했으며 최대거리3.4ms였다. 남는12개중10개는평가요청구간과겹쳤다. 이시간대조는요청ID증명이아니고 출처프로세스를식별하지못했으므로 외부작업이라고단정하지않는다.','- Ollama중지/단일vLLM모델적재와앱의호출횟수1회는확인했다. 추가로그는이앱의재시도로위장하지않고 별도감사로남겼다. 직접vLLM호출/공유사용의영향가능성이있으므로 이시간은참고실측이다. 다른작업을중지하거나앱/모델/서버를바꾸거나평가를재실행하지않았다.','- interference_audit.json, server_access_audit.json, server_access_timeline.json, latency_interference_flags.csv를확인한다. 미표시표본도상대요청의시작/지속시간을알수없어 완전무간섭을보장하지못한다.','', '## 정답·오답의 시간 (서버 정상·오류 구분과 별도)','', '|묶음|ID집합 정답 p50/p95 (n)|오답/오류 p50/p95 (n)|','|---|---|---|']
 for name in ['single','boundary_complex','conversation','scored_total']:
  g=summary['groups'][name];a=g['backend_id_correct'];b=g['backend_id_incorrect'];fmt=lambda v:f"{v['p50']:.3f}/{v['p95']:.3f}초 ({v['n']})" if v['n'] else '해당없음';extra.append(f"|{name}|{fmt(a)}|{fmt(b)}|")
 for name in ['report.md','comparison_report.md']:
  p=OUT/name;text=p.read_text();marker='표 시간은 현재 앱의 elapsed_seconds';text=text.replace(marker,caution+marker,1);p.write_text(text+'\n'.join(extra)+'\n')
 dump(OUT/'artifact_hashes.json',{str(p.relative_to(OUT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='artifact_hashes.json'})
 print(json.dumps({'language':language['by_actual_language'],'server_calls_fixed_window':audit['server_access_count'],'evaluation_calls':audit['actual_total_generation_calls'],'interference':summary['measurement_condition']},ensure_ascii=False))
if __name__=='__main__':main()
