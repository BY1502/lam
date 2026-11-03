"""Aggregate frozen real-chat and browser records only. No model imports/calls."""
import csv,difflib,hashlib,json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'evaluation/results/chat-fixes-20261102-01'
def load(name):return json.loads((OUT/name).read_text())
def write(name,obj):(OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def percentile(v,p):
 v=sorted(v)
 if not v:return None
 i=(len(v)-1)*p;lo=int(i);return v[lo]+(v[min(lo+1,len(v)-1)]-v[lo])*(i-lo)
def stats(v):
 v=[x for x in v if isinstance(x,(int,float))]
 return {'n':len(v),'p50':percentile(v,.5),'p95':percentile(v,.95),'min':min(v) if v else None,'max':max(v) if v else None}
def preserve():
 old=load('preservation_before.json');bad=[p for p,h in old['files'].items() if not (ROOT/p).is_file() or sha(ROOT/p)!=h]
 with sqlite3.connect(ROOT/'data/chatbot/sessions.sqlite3') as c:current={r[0]:hashlib.sha256(json.dumps(r,ensure_ascii=False).encode()).hexdigest() for r in c.execute('SELECT * FROM turns')}
 altered=[rid for rid,h in old['turns'].items() if current.get(rid)!=h]
 with (ROOT/'data/chatbot/requests.jsonl').open('rb') as f:prefix=hashlib.sha256(f.read(old['log_bytes'])).hexdigest()
 result={'protected_files':len(old['files']),'changed_protected_files':bad,'historical_turns':len(old['turns']),'changed_historical_turns':altered,'log_prefix_bytes':old['log_bytes'],'log_prefix_preserved':prefix==old['log_sha256']};write('preservation_after.json',result);assert not bad and not altered and result['log_prefix_preserved'];return result
def main():
 runs=[json.loads(l) for l in (OUT/'runs.jsonl').read_text().splitlines()];browser=load('browser_run.json');original=load('reported_originals.json');metrics=[]
 for x in runs:
  r=x['record'];t=r['timings'];a=r.get('analysis_stage') or {};b=r.get('response_stage') or {};u=b.get('usage') or {}
  metrics.append({'case':x['conversation']+'/'+str(x['turn']),'request_id':r['request_id'],'status':r['status'],'expected_ids':','.join(x['expected_ids']),'actual_ids':','.join(x['actual_ids']),'exact_ids':x['exact_ids'],'analysis_seconds':t.get('analysis_seconds'),'first_body_seconds':t.get('first_fragment_seconds'),'response_seconds':t.get('response_seconds'),'total_seconds':t['total_seconds'],'queue_seconds':t.get('queue_seconds'),'client_total_seconds':x['client_total_seconds'],'analysis_http_seconds':a.get('model_http_seconds'),'response_http_seconds':b.get('model_http_seconds'),'analysis_input_tokens':a.get('input_tokens'),'analysis_output_tokens':a.get('output_tokens'),'response_input_tokens':u.get('prompt_tokens'),'response_output_tokens':u.get('completion_tokens'),'generation_calls':r['llm_call_count'],'response_calls':r['response_call_count'],'actual_tool_calls':r['actual_tool_calls'],'validated_before_body':x['validated_before_body']})
 with (OUT/'metrics.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=metrics[0]);w.writeheader();w.writerows(metrics)
 summary={'main_requests':len(runs),'intent_set_exact':sum(x['exact_ids'] for x in runs),'completed':sum(x['record']['status']=='completed' for x in runs),'generation_calls_main':sum(x['record']['llm_call_count'] for x in runs),'generation_calls_browser':browser['record']['llm_call_count'],'actual_tool_calls':sum(x['record']['actual_tool_calls'] for x in runs)+browser['record']['actual_tool_calls'],'latency_main':{k:stats([r[k] for r in metrics]) for k in ['analysis_seconds','first_body_seconds','response_seconds','total_seconds','queue_seconds']},'body_completed_latency':stats([r['total_seconds'] for r in metrics if r['response_calls']]),'outside_only_latency':stats([r['total_seconds'] for r in metrics if not r['response_calls']]),'browser_status':browser['record']['status'],'language_review':'개발 AI가 10개 원본 분석/완료 답변과 브라우저 분석/부분 답변을 읽음. 이번 표본에서 중국어·일본어·요청하지 않은 영어 문장 혼입 관찰 안 됨. ID·slot·SOP 등 기술 표기 제외. 독립/블라인드 검토 아님.','semantic_review':'ID 일치와 설명의 의미를 별도 검토. grounded/3은 실제 계획이 있는 듯한 설정 표현과 모의 센서 도구 안내도 부적절. 업무 실행은 하지 않음.'};write('summary.json',summary)
 write('failures.json',{'mapping_failures':[{'case':r['case'],'request_id':r['request_id'],'expected_ids':r['expected_ids'],'actual_ids':r['actual_ids'],'reason':'이전 사용자 조건을 되묻는 후속 질문을 현재 상태 조회로 해석했다. 재질문은 없고 활성2대/제품A/내일은 유지됐지만 ID와 모의 도구 안내가 부정확.'} for r in metrics if not r['exact_ids']], 'implementation_verification_incident':'최초 읽기 전용 export 검증에서 과거 실패 행의 파싱 진단이 export envelope에 생성되지 않는 점을 발견. export도 같은 public(record) 함수를 거치도록 수정하고 단위/실제 GET·export·브라우저로 재검증. 생성 재시도 없음.'})
 preserved=preserve();changed=[];diff=[]
 for before in sorted((OUT/'before').rglob('*')):
  if not before.is_file():continue
  rel=before.relative_to(OUT/'before');now=ROOT/rel
  if now.is_file() and before.read_bytes()!=now.read_bytes():
   changed.append({'path':str(rel),'before_sha256':sha(before),'after_sha256':sha(now)});after=OUT/'after'/rel;after.parent.mkdir(parents=True,exist_ok=True);after.write_bytes(now.read_bytes());diff.extend(difflib.unified_diff(before.read_text().splitlines(True),now.read_text().splitlines(True),fromfile='before/'+str(rel),tofile='after/'+str(rel)))
 write('changed_files.json',changed);(OUT/'implementation.diff').write_text(''.join(diff));manifest=load('manifest.json');write('post_main_measurement_changes.json',[{'path':p,'initial_sha256':h,'final_sha256':sha(ROOT/p)} for p,h in manifest['hashes'].items() if (ROOT/p).is_file() and sha(ROOT/p)!=h]);write('final_hashes.json',{x['path']:x['after_sha256'] for x in changed})
 def fmt(v):return '—' if v is None else f'{v:.3f}'
 table='\n'.join(f"| {r['case']} | {r['actual_ids']} | {'일치' if r['exact_ids'] else '불일치'} | {fmt(r['analysis_seconds'])} | {fmt(r['first_body_seconds'])} | {fmt(r['response_seconds'])} | {fmt(r['total_seconds'])} | {r['generation_calls']} |" for r in metrics)
 oldtable='\n'.join(f"| {r['request_id']} | {fmt(r['timings'].get('analysis_seconds'))} | {fmt(r['timings'].get('first_fragment_seconds'))} | {fmt(r['timings'].get('total_seconds'))} | {r['status']} |" for r in original)
 outputs='\n\n'.join(f"### {x['conversation']}/{x['turn']} — {x['record']['request_id']}\n\n사용자: {x['question']}\n\n실제 의도: {', '.join(x['actual_ids'])}\n\n답변: {x['record']['reply'] or '[AI 본문 없음: 별도 범위 밖 상태]'}\n\n활성 조건(미확인 모델 해석):\n```json\n{json.dumps(x['record'].get('memory_snapshot'),ensure_ascii=False,indent=2)}\n```" for x in runs)
 report=f'''# 챗봇 업무 전환·참조·분류 설명·실패 감사 수정 결과

## 수정 원인과 공통 경로

기존 경로는 frontend/chatbot.js → POST messages → backend/chat_flow.py process → chat_analysis.validate → SQLite 저장 → response_messages → 실제 vLLM SSE/브라우저 NDJSON이다. 15개 의도는 미확정 검토안이고 분류 전용 테스트·1차66개 자료·모델 설정은 변경하지 않았다.

1. 업무 전환 오류: 원래 실패 원문은 knowledge.lookup/new_task를 정확히 반환했고 이전 target_product/production_date/packaging_equipment를 현재 발언 ‘다른 업무야.’ 근거로 remove했다. 서버는 remove가 있으면 무조건 상속이라고 오류 처리했다. new_task의 활성 초기화는 유지하면서 검증된 기존 슬롯의 해제만 허용했다. 존재하지 않는 슬롯·잘못된 현재 근거·이전 턴을 새 요청/updates에 상속하는 경우는 계속 거부한다. 완전 검증 후에만 새 memory_snapshot을 커밋한다. 원래 실패 JSON 자체도 새 검증기로 오프라인 통과함을 확인했으며 기존 오류 기록은 그대로다(original_failure_offline_validation.json, 모델 호출0회).
2. 맥락 없는 참조: 이전 프롬프트가 ‘명확한 업무에서 자료 부족이면 분류’를 강조했지만 참조 대상 부재와 단순 데이터 부족을 구분하지 않았다. 같은 세션의 참조 근거 유무를 보고 전자는 clarify, 후자는 원래 의도를 유지하게 명시했다. 문자열/키워드/정답 ID를 코드로 교정하지 않는다.
3. 분류 설명 지시: 복합 질문을 실제 업무 목적 단위로 나누고 출력 형식·분류 설명 지시는 별도 요청이 아니라고 명시했다. 실제 비제조 업무·업무 없는 ID 강요 정책은 유지했다.
4. 실패 원문: analysis_stage에는 원문이 이미 있었지만 public()의 미검증 분석 차단이 오류 종료 뒤에도 계속 적용돼 UI에 안 보였다. 공통 failure_details()가 원문·파싱 결과·파싱 오류·검증 사유·request_id를 미검증 감사 정보로 만들고 미래 오류는 저장하며 과거 행은 조회 때만 구성한다. 오류 종료 후 상세·내보내기에서만 보이고 pending 때는 숨긴다. 전체 export envelope가 아니라 내부 record에도 같은 public() 경로를 적용했다. 기존 DB의 과거 오류 원문/상태는 바꾸지 않았다. 별도 thinking/인증 헤더·키 필드는 제외한다.

분석 프롬프트 chat-analysis-v3.0.1, 검증 계약 chat-stream-contract-v3.0.1, 메모리 user-context-slots-v1.0.1이다. JSON 필드 구조·15개 정의·응답 프롬프트는 그대로다. 변경 파일은 changed_files.json/implementation.diff/before/after. 핵심은 backend/chat_analysis.py, chat_flow.py, chat_flow_schema.py, prompts/chat_analysis_v3.md, frontend/chatbot.js, tests/test_chat_stream.py다.

## 자동 검사와 실제 검증 구분

- pytest **83개 통과**(pytest.log). 정상 해제/잘못된 상속/없는 슬롯/실패 후 기존 메모리 불변/오류 원문·파싱/export/검증 전 숨김/실제 본문 없는 경우 첫 조각 시간 부재를 검사했다. 기존 Enter/중복ID/세션/중단/스트림/범위 밖 gate 검사도 유지했다. TestClient 관련 기존 외부 라이브러리 deprecation warning1개가 있다.
- 모의 HTTP 브라우저 입력 검사 **13조건 통과**. Enter·버튼 같은 경로, Shift+Enter, 공백, 중복 전송, 입력 초기화/초안, 같은ID복구, 안전한 표시, 세션 분리·새로고침, 연결 오류 후 재입력을 검사했다. fixture의 지연은 실제 모델 성능이 아니다.
- 실제 vLLM: 실행 전 questions_gold.json/sha256를 고정한 **6개 대화·10개 메시지**, 모두 완료. 기대 의도 집합 **9/10** 일치. 각 유효/clarify는 분석1+응답1, 순수 범위 밖1건은 분석1+응답0·AI본문 없음. 총19회. 자동 재시도/정답 보정/출력 수정 없음.
- 실제 브라우저: 추가1건을 Enter 전송해 실제 content 증가2회를 관찰한 뒤 사용자 취소. 분석1+응답1, 부분 답변 ‘{browser['record']['reply']}’와 interrupted를 저장하고 새로고침 복원. 18조건 통과, page error0. 이 중단은 의도한 검증이며 정상 완료 시간/정확도에 섞지 않는다. 저장된 업무 전환·clarify·복합·실제 범위 밖·원래 실패 감사 화면도 GET으로 재확인했다.
- 물리 OS 한글 IME는 **미검증**. 실제 headless Chromium에서 합성 composition/isComposing/keyCode229 이벤트를 사용했으며 물리 입력 검증이라고 주장하지 않는다. 실제 vLLM 장애/타임아웃 주입은 하지 않았고 자동 검사로 검증했다.

## 실제 입출력과 남은 한계

업무 전환 3턴은 work.plan → work.plan → knowledge.lookup으로 끝났다. 2턴은 제품A·내일을 유지하고 활성4대를2대로 대체했다. 3턴은 혼합기/세척 방법만 활성화하고 제품·날짜·대수는 상속하지 않았다. 이전 사용자 원문은 그대로 남는다.

새 세션의 ‘몇 대로 계획하기로 했지?’는 clarify가 됐고 실제 답변은 ‘어떤 생산 계획의 설비 대수를 확인하고 싶으신가요?’로 시작했다. 다른 세션의 대수는 가져오지 않았다. 구조화 interpreted_request의 ‘이전에 합의한’은 사용자가 가리키는 목적을 재서술한 표현이므로, reason/확인 질문의 근거 부재 설명과 함께 읽어야 한다. 합의가 검증됐다는 뜻은 아니다.

복합 질문은 status.query/data.analyze/cause.analyze/whatif.simulate **4개만** 반환했다. 어제 온도 ‘추이’는 현행 정의상 data.analyze로 정답을 고정했다. 분류 설명 지시로 만든 out_of_scope는 없었다. 여행 숙박이 섞인 별도 문항은 실제 범위 밖 항목을 유지했다.

**미해결 1건:** 같은 세션의 근거 있는 대수 후속 질문은 재질문하지 않았고 활성2대·제품A·내일을 보존했지만 history.query 대신 **status.query**를 반환했다. 답변의 ‘설정된 생산계획’ 표현과 최신 센서 도구 후보도 대화의 사용자 조건을 실제 시스템 상태처럼 보이게 할 수 있다. 실제 계획·조회·센서 결과를 생성한 것은 아니지만 의도/모의 도구 안내 실패로 기록했다. 정답을 바꾸거나 재생성하지 않았다. 슬롯/형식 검증만으로 의미 정확성을 보장할 수 없다. 새 업무 전환·참조·메타 지시는 모델 의미 판정이므로 다른 표현의 일반화 검증은 남는다.

이번11개 요청의 원본 분석/완료 본문9개/부분 본문1개를 개발 AI가 읽었다. 의도 ID·기술 약어를 제외한 중국어·일본어·요청하지 않은 영어 문장 혼입은 이 표본에서 관찰하지 못했다. 독립/블라인드/사람 의미 검토는 아니다.

## 단계별 시간(초)

| 사례 | 실제 의도 | 기대와 비교 | 분석 | 첫 실제 본문 | 응답 단계 | 전체 | 생성 호출 |
| --- | --- | --- | --- | --- | --- | --- | --- |
{table}

완료10건 전체 p50 **{summary['latency_main']['total_seconds']['p50']:.3f}초**, p95 **{summary['latency_main']['total_seconds']['p95']:.3f}초**. 본문 있는9건과 범위 밖1건의 통계는 summary.json에 분리했다. 소표본 p95를 안정적인 운영 지연으로 해석하지 않는다.

브라우저의 저장 수락 관측 {browser['browser_accept_seconds']:.3f}초, 첫 본문 DOM 관측 {browser['browser_first_body_seconds']:.3f}초, 중단 상태까지 {browser['browser_total_until_interrupted_seconds']:.3f}초. MutationObserver+5ms 대기 검사이며 정확한 첫 페인트가 아니다. 서버 첫 본문 시간은 분석 상태나 heartbeat가 아니라 실제 보이는 delta를 생성한 때다. 수락/대기/분석/응답/첫 본문/전체를 구분한다.

지연 근거: 복합4개 문항은 분석 {metrics[7]['analysis_seconds']:.3f}초·응답 {metrics[7]['response_seconds']:.3f}초로 구조화 분석이 가장 큰 부분이다. 입력/출력 토큰은 metrics.csv와 원문 stage에서 확인할 수 있다. 분석 단계는 목적·조건·근거·메모리를 모두 JSON으로 생성하며 응답 단계는 별도 호출이다. 이 관측만으로 GPU/캐시/공유 부하 원인을 확정하지 않는다. 모델/양자화/출력 한도/추론/병렬도 변경과 속도 최적화는 하지 않았다. think=false, temperature0, seed20261012, 분석4096/응답1536 유지. 기존 vLLM0.32.0·모델 alias qwen3.8:27b·FP8 명령/digest는 manifest.json. 별도 워밍업/캐시 초기화/재적재 없이 기존 실행 서버를 사용했고 다른 사용자의 작업을 중단하지 않았다.

아래는 신고된 원래 기록의 시간이다. 서로 다른 질문/컨텍스트/캐시 상태이므로 새 결과와 통제된 성능 전후 비교가 아니다. 원래 오류/시간을 수정하지 않았다.

| 원래 요청 ID | 분석 | 첫 본문 | 전체 | 상태 |
| --- | --- | --- | --- | --- |
{oldtable}

## 보존·실행·복원

기존 평가/원본 파일 {preserved['protected_files']}개, 이전 DB turn {preserved['historical_turns']}개, 감사 로그 앞 {preserved['log_prefix_bytes']}바이트의 해시가 유지됐다(preservation_after.json). 기존 분류 테스트 계약·정의·모의 도구 카탈로그·응답 프롬프트는 before와 동일하다. 1차66개는 추가하지 않았다. 실제 업무 검색/DB/예측/도구/승인/설비 제어 호출은0회다. environment_after.json에서 엔진 버전·image digest·실행 명령이 동일하고 실제 모델 payload에 tools가 없음을 확인했다. 접근 로그의 생성 POST21개는 자체 원본 호출19+2회와 수가 일치했으며 추가 POST는 관찰되지 않았다(로그에 요청ID가 없어 호출별 소유자를 독립 확정한 것은 아님). 기대 정답은 평가기에서만 비교하고 모델에는 현재 질문·제한된 같은 세션 맥락·공통 프롬프트만 보냈다.

실제10건 실행 중 모델 입력과 코드 해시는 고정했다(completion.json). 이후 과거 실패 export의 감사 경로 보완은 post_main_measurement_changes.json에 구분했고 자동 검사/실제 GET·export·브라우저로 확인했다. 분석 프롬프트/스키마/모델 설정은 바뀌지 않았다. 실패 harness의 최초 읽기 검증 KeyError도 failures.json에 원인과 수정 범위를 명시했다. 실제 모델 요청을 다시 실행한 것은 아니다.

```bash
cd /home/yt/lam
.venv/bin/pytest -q
.venv/bin/python evaluation/chat_fixes/read_only.py   # 저장된 오류/내보내기만 조회
.venv/bin/python evaluation/chat_fixes/report.py      # 저장 자료만 집계, 모델 호출 없음
# 앱: 기존 user service
systemctl --user restart food-lam-ui
```

접속 http://127.0.0.1:8080 (VS Code 원격이면8080 전달). 기록이 있는 run.py/browser.py는 완료 질문을 자동 재생성하지 않도록 보호한다. 결과 삭제/정답 변경 없이 새 실험을 분리해야 한다. 백업은 before/, 변경 패치는 implementation.diff, 복원은 rollback.md. 복원 시 모델/DB/이전 세션/평가 기록을 삭제하지 않는다.

## 실제 답변 원문과 활성 해석

{outputs}
'''
 (OUT/'report.md').write_text(report);(OUT/'comparison_report.md').write_text(report)
 (OUT/'rollback.md').write_text('''# 복원

처리 중 요청이 없는지 확인하고 웹 서비스만 중지한다. 모델/DB/감사 로그/과거 평가를 복원하거나 삭제하지 않는다.

```bash
cd /home/yt/lam
systemctl --user stop food-lam-ui
.venv/bin/python - <<'PY'
from pathlib import Path
import json,shutil
root=Path('/home/yt/lam');backup=root/'evaluation/results/chat-fixes-20261102-01'
for item in json.loads((backup/'changed_files.json').read_text()):
    old=backup/'before'/item['path']
    if old.is_file():shutil.copy2(old,root/item['path'])
PY
systemd-run --user --unit=food-lam-ui --property=WorkingDirectory=/home/yt/lam /home/yt/lam/.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8080
```

transient unit은 stop 후 없어질 수 있어 원래 실행 명령을 사용한다. 새 감사 필드가 있는 기록도 그대로 보존한다.
''')
 write('artifact_hashes.json',{str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and p.name!='artifact_hashes.json'})
 print(json.dumps({'main_exact':summary['intent_set_exact'],'requests':len(runs),'preservation':preserved,'report':str(OUT/'report.md')},ensure_ascii=False))
if __name__=='__main__':main()
