"""Final read-only provenance audit plus derived tables. No model generation."""
import csv,hashlib,json,subprocess,sys
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend import chatbot
from evaluation.score import percentile,ratio
from evaluation.chatbot_current.review import rows,export,generated
from evaluation.chatbot_current.score import correct,ids,good
from evaluation.run_classification import inspect,fingerprint
OUT=ROOT/'evaluation/results/chatbot-eval-20261029-01'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def main():
 m=json.loads((OUT/'manifest.json').read_text());data=rows(OUT/'runs.jsonl');ui=rows(OUT/'ui_runs.jsonl');warm=rows(OUT/'warmups.jsonl');gold={r['id']:r for r in json.loads((OUT/'questions_gold.json').read_text())['cases']};s=json.loads((OUT/'summary.json').read_text());groups=export();reviews=rows(OUT/'manual_reviews.jsonl')
 assert len(data)==414 and len(ui)==4 and len(warm)==1 and s['semantic_complete'] and len(reviews)==len(groups)
 assert all(sha(ROOT/f)==h for f,h in m['hashes'].items())
 assert all(r['record']['llm_call_count']==1 for r in [*data,*ui,*warm])
 assert fingerprint(inspect('vllm-qwen'))==m['model']['container_config_sha256']
 modeldir=Path(m['model']['checkpoint_path']);weights_unchanged=all((modeldir/f).stat().st_size==v['bytes'] and (modeldir/f).stat().st_mtime_ns==v['mtime_ns'] for f,v in m['model']['model_file_stats'].items());assert weights_unchanged
 accepted=[r['record'] for r in [*data,*ui,*warm]]
 raw_preserved=[]
 for r in accepted:
  raw=(r.get('raw_response') or {}).get('choices') or [];content=raw[0]['message'].get('content') if raw else None;raw_preserved.append(content==r.get('content'))
 assert all(raw_preserved)
 known={r['request_id'] for r in accepted}
 others=[]
 for r in rows(ROOT/'data/chatbot/requests.jsonl'):
  if r.get('created_at','')>=m['created_at'] and r.get('request_id') not in known:others.append({'request_id':r.get('request_id'),'created_at':r.get('created_at'),'completed_at':r.get('completed_at'),'model':r.get('model'),'llm_call_count':r.get('llm_call_count')})
 try:
  p=subprocess.run(['docker','logs','--since',m['created_at'],'--until',max(r['completed_at'] for r in accepted),'vllm-qwen'],capture_output=True,text=True,timeout=30);access=[line for line in (p.stdout+'\n'+p.stderr).splitlines() if 'POST /v1/chat/completions HTTP/' in line];access_count=len(access)
 except Exception:access=[];access_count=None
 dump(OUT/'server_access_audit.json',{'since':m['created_at'],'until_fixed_last_own_response':max(r['completed_at'] for r in accepted),'recorded_chat_completion_access_lines':access_count,'expected_own_generation_calls':419,'other_app_record_metadata':others,'access_lines':access,'limitation':'access log has no eval-case request_id; count agreement is supporting evidence, not proof of arbitrary remote clients absence'})
 previous=defaultdict(list);message_invariants=[]
 for r in data:
  sid=r['session_id'];rr=r['record'];expected,_,_=chatbot.build_messages(gold[r['case_id']]['question'],previous[sid]);actual=rr.get('request',{}).get('messages');message_invariants.append(expected==actual);previous[sid].append(rr)
 assert all(message_invariants)
 base=(ROOT/'prompts/chatbot_v2.md').read_text()+'\n\n분류 정의:\n'+json.dumps(chatbot.load_taxonomy(),ensure_ascii=False)
 leak=any(c['question'] in base for c in gold.values());assert not leak
 by_case=defaultdict(list)
 for r in data:by_case[r['case_id']].append(r)
 stability=[]
 for iid,rs in by_case.items():
  times=[r['record']['elapsed_seconds'] for r in rs if r['record'].get('elapsed_seconds') is not None];correct_count=sum(correct(r,gold) for r in rs) if gold[iid]['scored'] else None
  stability.append({'case_id':iid,'suite':gold[iid]['suite'],'repeats':len(rs),'correct_runs':correct_count,'all_three':correct_count==3 if correct_count is not None else None,'some':correct_count in [1,2] if correct_count is not None else None,'all_wrong':correct_count==0 if correct_count is not None else None,'backend_min':min(times) if times else None,'backend_max':max(times) if times else None,'range':max(times)-min(times) if times else None,'times':times,'ids':[ids(r) for r in rs]})
 dump(OUT/'question_repeat_stability.json',stability)
 with (OUT/'question_repeat_stability.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(stability[0]));w.writeheader();w.writerows(stability)
 # Explicit UI review after reading the four generated responses. Caller writes it before this audit.
 urev=json.loads((OUT/'ui_semantic_review.json').read_text());assert len(urev)==4
 audit={'ended_at':datetime.now(timezone.utc).isoformat(),'actual_chat_route':True,'model':m['model']['served_model_api'],'versions':m['versions'],'planned_evaluation_calls':414,'actual_evaluation_calls':414,'warmup_calls_excluded':1,'ui_sample_calls_separate':4,'actual_total_generation_calls':419,'exactly_one_call_per_sent_user_message':True,'no_tools':all('tools' not in r['request'] and not r.get('tool_calls') for r in accepted),'canonical_request_reconstruction_match':all(message_invariants),'gold_or_question_list_in_system':False,'all_input_hashes_unchanged':True,'model_container_config_unchanged':True,'checkpoint_sizes_mtimes_unchanged':weights_unchanged,'raw_model_content_preserved':True,'app_code_modified':False,'generated_response_retries':0,'output_replacements_or_gold_corrections':0,'other_app_records_during_experiment':len(others),'server_access_count':access_count,'unattributed_generation_posts_within_fixed_window':access_count-419 if access_count is not None else None,'latency_isolation':'unverified: extra server generation requests with no application conversation records; see interference_audit.json','semantic_reviewed_responses':sum(len(g['members']) for g in groups),'semantic_unique_groups':len(groups),'review_judgments_fixed_sha256':sha(OUT/'manual_reviews.jsonl'),'review_groups_sha256':sha(OUT/'review_groups.json'),'review_method':'Current AI read each distinct generated text with actual question+user context; identical generated text+context groups share a reading judgment. No internal thinking. Not independent/blind/human-certified.','ui_report_read_check':json.loads((OUT/'ui_checks.json').read_text())}
 dump(OUT/'final_audit.json',audit)
 extra=['','## 최종 감사·반복 변동·UI 검토 (추가 기록)','',f"총 생성 {audit['actual_total_generation_calls']}회 = 평가414 + 워밍업1 + UI4. 사례별 원본생성 내용은 API content와 정확히 동일했다. 모든414건의 전송 messages를 당시세션의 이전 원문으로 재구성했을 때 정확히일치했다. 다른세션근거혼입을 검출하지 못했다.",f"현재앱 코드/프롬프트/정의/설정 해시 보존. 모델컨테이너구성 및 체크포인트 크기/mtime 보존. 실제서버접근로그의 생성POST {access_count}개. 평가외 앱대화기록 {len(others)}개. 접근로그는 사례ID와직접연결되지않으므로 외부클라이언트부재의절대증거로 과장하지 않는다.",'','|반복|요청|오류|백엔드 p50/p95|min/max|모델HTTP p50/p95|입력/출력 토큰|','|---|---:|---:|---|---|---|---|']
 for rep,v in s['per_repeat'].items():extra.append(f"|{rep}|{v['n']}|{v['errors']}|{v['backend_all']['p50']:.3f}/{v['backend_all']['p95']:.3f}|{v['backend_all']['min']:.3f}/{v['backend_all']['max']:.3f}|{v['http_all']['p50']:.3f}/{v['http_all']['p95']:.3f}|{v['input_tokens']}/{v['output_tokens']}|")
 extra+=['','|묶음|backend min/max|모델HTTP min/max|','|---|---|---|']
 for key,v in s['groups'].items():
  if key=='scored_total':continue
  extra.append(f"|{key}|{v['backend_all']['min']:.3f}/{v['backend_all']['max']:.3f}|{v['http_all']['min']:.3f}/{v['http_all']['max']:.3f}|")
 finalst=[v for v in stability if gold[v['case_id']]['scored']];extra+=['',f"고유정답확정 질문/턴135개: 3회모두정답 {sum(v['all_three'] for v in finalst)}개, 1~2회정답 {sum(v['some'] for v in finalst)}개, 3회모두오답 {sum(v['all_wrong'] for v in finalst)}개. 대화는 각3턴도10개독립대화×3실행으로 별도집계했다. 상세는 question_repeat_stability.csv.",'','UI4개원문도 읽고언어·설명·매핑을확인했다. 이는 별도4개표본이며414건정확도/시간과섞지않는다.']
 for v in urev:extra.append(f"- {v['ui_sample']}: {v['language']} / {v['explanation']} — {v['reason']}")
 extra+=['','최종 검토 파일의 SHA256과 건수는 final_audit.json에 기록했다. 원본·정답·실패를 수정하지 않았다. 실제 제조 데이터나 업무결과를 평가한 것이 아니라 현재 챗봇의 의도분석·설명 응답만 평가했다.']
 for name in ['report.md','comparison_report.md']:
  p=OUT/name;p.write_text(p.read_text()+'\n'.join(extra)+'\n')
 dump(OUT/'artifact_hashes.json',{str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='artifact_hashes.json'})
 print(json.dumps(audit,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
