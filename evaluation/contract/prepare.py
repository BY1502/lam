from pathlib import Path
import json,hashlib,shutil,sqlite3
ROOT=Path('/home/yt/lam');OUT=ROOT/'evaluation/results/chatbot-contract-20261029-01';OUT.mkdir(parents=True,exist_ok=True)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
files=['backend/chatbot.py','backend/chat_schema.py','backend/main.py','backend/classifier.py','backend/config.py','backend/llm_client.py','prompts/chatbot_v2.md','taxonomy/intents_v2_ko.json','taxonomy/intents_v2.json','frontend/chatbot.js','frontend/app.css','frontend/index.html','tests/test_chatbot.py','evaluation/check_chatbot.py','evaluation/check_chatbot_language.py','README.md']
for name in files:
 dest=OUT/'before'/name;dest.parent.mkdir(parents=True,exist_ok=True)
 if dest.exists():raise RuntimeError('백업을 덮어쓰지 않습니다.')
 shutil.copy2(ROOT/name,dest)
old=ROOT/'evaluation/results/chatbot-eval-20261029-01';gold=json.loads((old/'questions_gold.json').read_text());rs=[json.loads(l) for l in (old/'runs.jsonl').read_text().splitlines()];errors=[]
for r in rs:
 v=r['record']
 if v.get('error_kind') is None:continue
 a=json.loads(v['content']);q=v['question'];unmatched=[]
 for req in a['requests']:
  texts=[q]+[x['quote'] for x in req['context_used']]
  for x in [*req['target_expressions'],*req['conditions'],*([req['time_expression']] if req['time_expression'] else [])]:
   if not any(x in text for text in texts):unmatched.append(x)
 errors.append({'case_id':r['case_id'],'repeat':r['repeat'],'request_id':v['request_id'],'question':q,'unmatched_interpretation_strings':unmatched,'old_error':v['error'],'old_raw_output':v['content'],'note':'추출계약 실패의 재현근거. 의미정답을 뜻하지 않으며 원문/점수를 바꾸지 않음.'})
with sqlite3.connect(ROOT/'data/chatbot/sessions.sqlite3') as conn:
 if conn.execute("SELECT count(*) FROM turns WHERE status='pending'").fetchone()[0]:raise RuntimeError('현재앱분석진행중; 재시작하지말고대기')
 turnhash={r[0]:hashlib.sha256(json.dumps(list(r),ensure_ascii=False).encode()).hexdigest() for r in conn.execute('SELECT request_id,session_id,created_at,question,status,record_json FROM turns')}
log=(ROOT/'data/chatbot/requests.jsonl').read_bytes()
protected={str(p.relative_to(ROOT)):sha(p) for parent in [ROOT/'evaluation/results',ROOT/'data/chatbot/validation'] for p in parent.rglob('*') if p.is_file() and OUT not in p.parents}
protected.update({p:sha(ROOT/p) for p in ['backend/classifier.py','backend/config.py','backend/schemas.py','prompts/classification_v2.md','taxonomy/intents_v2.json','taxonomy/intents_v2_ko.json']})
(OUT/'preservation_before.json').write_text(json.dumps({'files':protected,'turn_hashes':turnhash,'log_prefix_bytes':len(log),'log_prefix_sha256':hashlib.sha256(log).hexdigest()},indent=2))
(OUT/'baseline_failure_analysis.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2))
(OUT/'baseline_gold_unchanged.json').write_bytes((old/'questions_gold.json').read_bytes())
(OUT/'implementation_plan.md').write_text('''# 원문 근거·해석 계약 개선 계획

## 조사 결과
chatbot.build_messages → llm_client.infer 1회 → chatbot.validate → SQLite/JSONL → chatbot.js.
validate는 target_expressions/time_expression/conditions와 context_used.quote를 모두 부분문자열로 검증했다. 앞의3개는 사실상 자연어 해석이라 조사생략·숫자정리·어구결합도 거절됐다. 기존29개 오류는11개 고유문항이며 2개 대화경로를 포함한다. baseline_failure_analysis.json에 원시출력과 불일치표현을 보존했다. 의미정답은 별도 검토가 필요하다.

## 변경 순서와 계약
1. 기존코드/프롬프트/평가/원문을 백업·해시 고정.
2. 정리한 대상·기간·조건 필드는 유지하되 해석으로 정의한다. context_used를 모든요청의 evidence=[{turn_id,quote}]로 교체하여 현재/이전 사용자인용을 통합한다. 각요청과 근거는 같은항목 내에 연결한다. 해석별중복인용구조를 만들지 않는다.
3. 현재 사용자 request_id를 생성전에 이미 저장한 실제턴ID로 모델에 전달한다. 같은세션·전달범위의 실제user텍스트만 신뢰원문으로 검증한다. 인용은 연속구절 그대로 검사하고 해석에는 부분문자열을 요구하지 않는다.
4. JSON/스키마와 출처오류를 나누고 의미는not_automatically_verified로 표시한다. 정규화된해석의 틀린숫자/목적·허위업무사실은 의미검토 영역임을 부정테스트로 보인다.
5. 과거assistant원문은 저장/UI에서 그대로 보존하되 모델맥락에는 기존의도ID만 미확인해석으로 제공한다. 업무결과·구체제안·수행사실은없다는메타데이터를 명시한다. 이전user발언은원문 그대로 사용한다.
6. UI에서 해석/원문인용/이전맥락과검증범위를 구분, 과거context_used도읽도록유지.
7. 부정/회귀검사 후 기존414건과 새묶음을 각3회 실제챗봇API로 실행, 원문·정답·시간·검토·비교를 새디렉터리에 저장한다.

## 재사용/유지
기존vLLM전송·시간·저장·세션·모델lock·UI·분류정의15개·분류테스트계약을 유지한다. tools·업무실행·두번째LLM검증·자동재시도·응답치환은 추가하지 않는다. 다른API사용작업은중지하지않고공유서버접근로그를기록한다. 옛평가점수는변경하지않는다.
''')
print('backup done; errors',len(errors),'unique cases',len({e['case_id'] for e in errors}),'protected',len(protected),'old turns',len(turnhash))
