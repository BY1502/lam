"""Read-only final reporting. Never invokes a model or changes the application."""
import hashlib,json,sqlite3,subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import score
ROOT=score.ROOT;OUT=score.OUT
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name,x):score.write(name,x)
def fmt(x):return '미측정' if x is None else f'{x:.3f}'
def ratio(item):
 n=item['numerator'];d=item['denominator'];return f'{n}/{d} ({100*n/d:.2f}%)' if d else '해당 표본 없음'
def main():
 score.main();summary=score.load('summary.json');assert summary['saved_evaluation_cases']==72 and summary['reviewed_cases']==72,'최종 보고는72건 실제 실행·의미검토 후에만 생성'
 old=score.load('preservation_before.json');bad=[p for p,h in old['code'].items() if sha(ROOT/p)!=h];old_bad=[p for p,h in old['old_results'].items() if not (ROOT/p).is_file() or sha(ROOT/p)!=h]
 with sqlite3.connect('file:'+str(ROOT/'data/chatbot/sessions.sqlite3')+'?mode=ro',uri=True) as c:stored={r[0]:hashlib.sha256(json.dumps(r,ensure_ascii=False).encode()).hexdigest() for r in c.execute('SELECT * FROM turns')}
 changed_turns=[rid for rid,h in old['turns'].items() if stored.get(rid)!=h]
 with (ROOT/'data/chatbot/requests.jsonl').open('rb') as f:prefix=hashlib.sha256(f.read(old['log_bytes'])).hexdigest()
 preservation={'code_files':len(old['code']),'changed_code_files':bad,'historical_result_files':len(old['old_results']),'changed_result_files':old_bad,'historical_turns':len(old['turns']),'changed_historical_turns':changed_turns,'original_log_prefix_preserved':prefix==old['log_sha256']};write('preservation_after.json',preservation);assert not bad and not old_bad and not changed_turns and preservation['original_log_prefix_preserved']
 rows=score.rows('runs.jsonl');gold=score.load('questions_gold.json');questions=score.load('question_results.json');reviews={r['key']:r for r in score.rows('reviews.jsonl')};browser=score.rows('browser_checks.jsonl');all_messages=rows+score.rows('warmup.jsonl')+score.rows('control.jsonl');all_records=[x['record'] for x in all_messages if x.get('record')];manifest=score.load('manifest.json');completion=score.load('completion.json');audit=score.load('contract_audit.json')
 assert len({x['session_id'] for x in rows if x['phase']=='evaluation'})==72
 official=[x for x in rows if x['phase']=='evaluation'];failures=score.load('failures.json');overall=summary['overall'];main_errors=[x for x in official if (x.get('record') or {}).get('status')!='completed'];control=score.rows('control.jsonl')[0]['record']
 start=datetime.fromisoformat(manifest['created_at']).astimezone(ZoneInfo('Asia/Seoul'));end=datetime.fromisoformat(completion['at']).astimezone(ZoneInfo('Asia/Seoul'))
 labels={'independent_compound':'독립 복합(1~9)','single_subtasks':'단일·하위 작업(10~12)','format_instruction':'출력 지시(13·15)','mixed_scope':'제조+범위 밖(14)','conditional_or_intra_reference':'조건부·문장 내 참조(16~18)','synthetic_context':'합성 사전 맥락(19~20)','session_context':'정정·전환·후속(21·22·24)','missing_reference':'참조 부재(23)'}
 group_table='\n'.join(f"| {labels[g]} | {a['n']} | {ratio(a['exact_intent_set'])} | {a['completed']} | {sum(a['errors'].values())} | {fmt(a['completed_first_body_latency']['p50'])} / {fmt(a['completed_first_body_latency']['p95'])} | {fmt(a['completed_with_body_latency']['p50'])} / {fmt(a['completed_with_body_latency']['p95'])} |" for g,a in summary['by_group'].items())
 qtable=[]
 for q in questions:
  cells=[]
  for r in q['runs']:cells.append(('✓ ' if r['exact'] else '✗ ')+('/'.join(r['actual_ids']) or '[채택 분석 없음]')+(' · '+r['status'] if r['status']!='completed' else ''))
  qtable.append(f"| {q['question_id']} | {' + '.join(q['expected_ids'])} | {' | '.join(cells)} | {q['exact_count']}/3 | {'일치' if q['intent_set_repeat_consistent'] else '불일치'} |")
 semantic_table='\n'.join(f"| {k} | {overall['semantic_review'][k]['통과']}/72 | {overall['semantic_review'][k]['부분 통과']} | {overall['semantic_review'][k]['실패']} | {overall['semantic_review'][k]['근거 부족']} |" for k in score.FIELDS)
 notes=[]
 for x in official:
  r=x['record'];rev=reviews[x['key']]
  if x['key'] not in {f['key'] for f in failures}:continue
  notes.append(f"### {x['key']} / {r['request_id']}\n\n질문: {x['question']}\n\n기대: {', '.join(next(g['expected_ids'] for g in gold['items'] if g['id']==x['question_id']))}\n\n실제 채택: {', '.join(q['intent_id'] for q in (r.get('analysis') or {}).get('requests',[])) or '[없음]'}\n\n상태/오류: {r['status']} / {r.get('error_kind')} / {r.get('error')}\n\nAI 검토: {rev['note']}\n\n원본 분석(미검증이면 그대로 표시):\n```json\n{(r.get('analysis_stage') or {}).get('content','[미수신]')}\n```\n\n원본 본문: {r.get('reply') or '[없음]'}")
 (OUT/'failure_evidence.md').write_text('# 실패·부분 통과 근거 — 개발 AI 검토\n\n'+'\n\n'.join(notes))
 full=[]
 for x in official:
  r=x['record'];rev=reviews[x['key']];full.append(f"## {x['key']} / {r['request_id']}\n\n질문: {x['question']}\n\n검토: {json.dumps({k:rev[k] for k in score.FIELDS},ensure_ascii=False)}\n\n사유: {rev['note']}\n\n본문: {r.get('reply') or '[없음]'}\n\n분석/활성 맥락:\n```json\n{json.dumps({'analysis':r.get('analysis'),'failure':r.get('analysis_failure'),'memory_snapshot':r.get('memory_snapshot')},ensure_ascii=False,indent=2)}\n```")
 (OUT/'review_notes.md').write_text('# 72건 전체 원문 검토 — 개발 AI 판정(독립/블라인드/사람 검토 아님)\n\n'+'\n\n'.join(full))
 report=f'''# 현재 챗봇 복합 질의 평가

평가 시각: {start:%Y-%m-%d %H:%M:%S} ~ {end:%Y-%m-%d %H:%M:%S} Asia/Seoul. 원본 로그는 UTC를 보존한다. 분류체계 2차정리는 미확정 검토안이다. 실제 업무 검색·분석·예측·도구 실행·설비 제어의 정확도를 평가한 결과가 아니다.

| 평가 묶음 | 사례 수 | 의도 집합 정확 일치 | 정상 완료 | 오류 | 수락→첫 본문 p50/p95(초) | 수락→완료 p50/p95(초) |
| --- | --- | --- | --- | --- | --- | --- |
| 전체 | 72 | {ratio(overall['exact_intent_set'])} | {overall['completed']} | {len(main_errors)} | {fmt(overall['completed_first_body_latency']['p50'])} / {fmt(overall['completed_first_body_latency']['p95'])} | {fmt(overall['completed_with_body_latency']['p50'])} / {fmt(overall['completed_with_body_latency']['p95'])} |
{group_table}

표의 시간 시작점은 기존 백엔드의 SQLite 저장 수락 뒤 Job 등록 시점이며 HTTP 소켓 수신 시각은 아니다. 오류를 제외한 정상 종료·본문 있는 실행을 집계했다. ID 오분류여도 전송·생성이 정상 종료됐으면 이 시간 표에는 포함하며 정답 성공과 구분한다. ID까지 맞은 실행만의 시간은 summary.json/exact_id_success_latency에 따로 있다. 소표본 유형별 p95는 안정적인 운영 성능이 아니다.

## 수량·실행 조건

- 독립 질문24개 × 반복3회 = **72개 채점 사례**. 문항마다 반복별 새 세션72개. 반복을72개 서로 다른 독립 질문으로 해석하지 않는다.
- 채점 대상 사용자 턴72개 + 준비 턴15개 = **{summary['evaluation_dialog_user_messages']}개 평가 대화 사용자 메시지**. 준비 턴은 정확도에 합치지 않았다.
- 별도 준비 요청 {summary['warmup_user_messages']}개, 순수 범위 밖 제어 표본 {summary['outside_control_user_messages']}개를 포함한 전체 실제 사용자 메시지는 **{summary['all_recorded_user_messages']}개**다.
- 생성 API 요청은 실제 기록 합계 **{summary['generation_calls']}회**: 채점 턴 {summary['generation_calls_evaluation']}회, 사전 맥락/1턴 준비 {summary['generation_calls_preparation']}회, 별도 표본 {summary['generation_calls_separate']}회. 실패한 생성도 포함한다. 연결 probe는 생성 호출이 아니다.
- 현재 vLLM Qwen {manifest['model_probe']['model']}, 버전 {manifest['engine']['version']}, think=false, temperature0, seed20261012, 분석4096/응답1536 출력 한도를 유지했다. image/digest/실행 명령·GPU 선택은 manifest.json에 있다. 캐시 초기화·모델 재적재·설정 변경은 하지 않았고 준비 요청은 별도 기록했다. 다른 이용자의 API 부하는 통제하지 않았고 다른 작업을 종료하지 않았다. 자체 요청은 순차이고 다른 성능 실험을 실행하지 않았다.
- 최초18문항/23번은 새 세션 첫 질문, 19~20번은 사용자 제공 합성 사전 맥락 뒤 질문, 21~22·24번은 제공1턴 뒤2턴이다. SIM-001/PROP-001은 테스트 참조일 뿐 실제 작업/추천 결과로 주입하지 않았다. 기존 API가 준비 발언도 분석하기 때문에 그 응답/분류/시간을 따로 보존했으며, 정상 업무 결과로 취급하지 않았다.

## 정답 고정·비변경 확인

dataset_source.md에 제공 문구를 보존했다(표 정렬 공백만 정리). questions_gold.json의 질문·정답·판정 기준·합성 맥락 전달문·실행 순서를 모델 호출 전에 SHA256로 고정했다. preflight.md에는 정책 대조와 20/23번 경계를 기록했다. 실행 후 정답/허용 기준을 바꾸지 않았다.

실제 경로는 기존 챗봇 POST messages → 분석·검증 → 응답 gate → 실제 vLLM 스트림이다. 짧은 분류 전용 API를 사용하지 않았다. 모델에는 사용자 질문·제한된 같은 세션 맥락·기존 공통 정의만 전달했고 정답표/전체 질문 목록은 전달하지 않았다. 실제 요청 원문과 구조화 분석은 runs.jsonl에 있다.

기능/프롬프트/분류체계/설정 파일 **{preservation['code_files']}개 변경0**, 기존 결과 파일 {preservation['historical_result_files']}개 변경0, 기존 DB 메시지 {preservation['historical_turns']}개 변경0, 기존 감사 로그 접두부 해시 보존을 확인했다. 보존용 인벤토리에 과거 분류 파일 해시가 있어도 1차66개를 모델 입력이나 평가 질문에 사용하지 않았다. 자동 재시도·대체 모델·출력 치환·정답 보정·선택적 재실행은 없다. 저장 결과/보고서 재열람은 GET만 사용한다. 대화는 챗봇 세션/감사 기록으로 저장되며 학습·골든셋·업무 사실 DB에 편입하지 않았다.

## 문항별3회 결과

| 번호 | 고정 기대 의도 | 1회 | 2회 | 3회 | 정답 | 의도 집합 반복 일치 |
| --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(qtable)}

세 번 모두 정답인 질문 {summary['question_all_three_exact']}/24개. 세 번의 의도 집합이 같은 질문 {summary['question_consistent']}/24개. 반복 일치는 정확도와 다르며, 같은 오답/같은 오류도 별도로 표시한다. 미검증 원문의 ID는 진단용으로 보존하되 채택 결과로 정답 처리하지 않았다.

## 의미 검토(자동 지표와 별도)

개발 AI가72건의 질문·각 실제 요청 해석·분류 이유·조건·출처·활성 메모리·본문과 관련 준비 턴을 읽고 판단했다. 모델 자체를 다시 호출해 판정하지 않았다. 독립/블라인드/사람 검토 또는 현장 정확도가 아니다. 부분 통과/근거 부족을 성공에 합치지 않았다. 판정 원문은 reviews.jsonl·review_notes.md, 자동 결과는 scored.csv다.

| 항목 | 통과 | 부분 통과 | 실패 | 근거 부족 |
| --- | --- | --- | --- | --- |
{semantic_table}

의도 집합과 위6개 항목을 모두 통과한 교집합은 **{overall['strict_intersection_pass']}/72**다. 이는 스트림 종료 프로토콜까지 포함한 전체 시스템 통과율이 아니다. 가중치 종합점수는 만들지 않았다. 누락 의도 인스턴스 {overall['missing_intent_instances']}개, 추가 의도 인스턴스 {overall['added_intent_instances']}개, 중복 ID 인스턴스 {overall['duplicate_intent_instances']}개다. 중복 ID와 과도한 요청 분리는 동일하지 않다. 예를 들어23번은 서로 다른 참조 대상(적용할 계획/비교할 결과)을 각각 묻는 두clarify가 유효하므로 ID중복 자체만으로 분리 실패 처리하지 않았다. 문서 조회 항목·출력 지시·분석 하위 조회는 제공 기준에 따라 별도로 판단했다.

언어 검사는 reply뿐 아니라 목적/분류 이유/확인 질문/정규화 조건/활성 메모리 등 생성 문장을 읽었다. 문자 검출은 후보 수집에만 사용했고 language_review.csv에 원문 문맥과 허용/혼입 판정을 남겼다. 의도/도구 ID, 모델명, SOP, SIM-001/PROP-001 등 기술 표기는 혼입에서 제외했다. 응답 자체가 없는 오류의 최종 설명 의미는 근거 부족으로 남겼다. 이 표본에서의 관찰이지 언어 혼입이 불가능하다는 보장은 아니다.

## 실행 안정성·스트리밍·시간

채점 턴의 정상 완료 {overall['completed']}건, 오류 {len(main_errors)}건. 오류 종류 {json.dumps(overall['errors'],ensure_ascii=False)}. 준비/제어까지 포함한 상태는 {json.dumps(summary['all_message_statuses'],ensure_ascii=False)}, timeout {summary['timeouts_all_messages']}건이다. 준비 실패와 실제 생성 실패, JSON 문법과 스키마/출처 검증을 분리했다. 원시 분석 JSON은 제공 {summary['formal_analysis_json']['provided']}건 중 파싱 가능 {summary['formal_analysis_json']['parseable']}건이다. JSON 파싱 성공을 정상 분류/의미 정확성으로 간주하지 않는다.

세부 분석/첫 본문/전체/토큰/종료 사유는 message_metrics.csv에87개 대화 메시지와 별도2개를 구분했다. summary.json에는 유형별 합계·분모가 있다. 분석 HTTP와 응답 HTTP도 분리하며 미수신 토큰/시간은 공백/null이다. heartbeat/상태/빈 문자열은 첫 본문으로 집계하지 않는다. 첫 본문은 기존9문자 안전 버퍼를 거친 사용자 노출 content delta 기준이다.

서버 수신→분석 완료의 독립 타임스탬프는 현재 구현에 없어 **정확한 서버 기준 값은 미측정**이다. 대기+분석 시간을 더해 추정하지 않았다. 대신 API 클라이언트 송신→analysis 이벤트 수신을 관측 시간으로 별도 기록했다. 분석 LLM 소요/백엔드 저장 수락→첫 본문/저장 수락→백엔드 종료는 기존 텔레메트리를 썼다. 정확한 HTTP 수신→첫 본문/완료도 별도 계측이 없어 미측정으로 구분한다. API 클라이언트 송신→첫 nonempty delta는 summary.json의 client_first_body_observed에 기록했다. 완료 이벤트는 실제로 관찰되지 않아 해당 시간은 미측정이다. 송신→스트림 EOF 뒤 저장 종료 상태 확인 시간은 client_stored_done_confirmed로 별도 기록했다. 브라우저 표본의 raw client_total_seconds에는 스크린샷·재열람까지 포함되므로 균일 응답시간으로 쓰지 않고 message_metrics.csv에서 browser_operation_with_readback_seconds로 분리했다. UI 완료 시간은 사전 지정한10/13/22번1회차3건에서만 실제 관측했으며 나머지는 측정하지 않았다.

브라우저3건은 모두 실제 본문이 완료 전에 여러 번 증가하고, 저장된 최종 본문과 UI가 같으며, 새로고침 후 본문/상태가 복원되고 추가 메시지 제출이 없음을 확인했다. browser_checks.jsonl에는 DOM 관측값·샘플·검사 결과가 있고 ui_*.png에 화면이 있다. MutationObserver 기반 관측이며 정확한 첫 페인트 측정은 아니다. API events/브라우저 상태에서는 검증 전 본문이 노출되지 않았는지도 점검했다.

순수 범위 밖 별도 제어 표본의 실제 결과는 {control['status']}, 응답 생성 **{control['response_call_count']}회**, 본문 {len(control.get('reply') or '')}자다. 이 표본은24문항 정답률 분모에 넣지 않았다. 제조+여행 혼합 문항은 제조 부분 설명과 범위 밖 상태를 별도로 검토했다.

### 추가 발견: 종료 이벤트 전달 누락
+
+원본 이벤트를 수집한 API 스트림86건에서 complete/error 이벤트는0건이었다(본문이 없는2개 오류도 포함). 네트워크 예외는 없었으며 EOF 후 SQLite 저장 종료 상태를 확인했다. UI는 frontend/chatbot.js의 watch finally에서 저장 결과를 GET으로 재조회하므로3개 실제 브라우저 표본의 최종 본문·상태는 올바르게 표시됐다. 따라서 ‘최종 UI 일치’와 ‘정상 종료 이벤트 전달’을 같은 성공으로 합치지 않는다. 이 문제가 의도 오분류를 뜻하지는 않으며 별도 프로토콜 결함으로 기록한다.
+
+코드 확인: backend/chat_flow.py:220~222에서 available을 조건대기 전에 계산하고, 대기 후 재계산 없이 job.ended이면 반환하는 분기가 있다. 종료 직전 이벤트가 이 경합에서 빠질 가능성이 높지만 스레드 스케줄 추적/수정 후 재검증은 하지 않았으므로 최종 원인 확정과 구분한다. 현재 EOF/저장조회로 복구되는 흐름을 바꾸거나 결과를 재실행하지 않았다. contract_audit.json/stream_termination_audit.json에 실행별 증거를 보존했다.
+
+## 실패 근거와 개선 우선순위(이번에는 미적용)

각 실패/부분 통과의 질문·기대·실제·요청ID·원문은 failure_evidence.md와 failures.json을 참조한다. 추론 수준·모델/프롬프트/도구를 고쳐 점수를 다시 측정하지 않았다.

1. **종료 프로토콜 우선:** 위86건 종료 이벤트 누락을 재현하고 조건대기/이벤트 소모 경계를 별도 수정·검증할 필요가 있다. 실제 본문 생성과 저장은 확인했으나 종료 신호를 UI의 재조회가 대신하고 있었다. 이번에는 수정하지 않았다.
2. **확인된 사실:** 24번 계열의 원문에서 분석을 위한 이력 조회를 독립 요청으로 추가하거나, 기존 메모리를 변경하지 않으면서 updates로 재발행하고 이전 턴 근거만 넣는 문제가 관찰됐다. 실제 source_error와 원문을 사례별로 분리했다. **원인 추정/제안:** 하위 조회 분리와 변경 없는 슬롯 재전달의 계약 이해가 불안정할 수 있다. 변경된 슬롯과 기존 슬롯 유지의 프롬프트/계약 경계를 다음 별도 작업에서 검토할 우선순위가 높다. 현재 검증을 완화하거나 ID를 자동 보정하지 않았다.
3. **확인된 사실:** 13번의 ‘어제 온도 추이’가 분석 대신 단순 이력 조회로 연결되는 오답과 17번의 누락 조건 미표시가 관찰됐다. **원인 추정/제안:** 명사형 요청을 목적 문장으로 해석하는 단계와 의도 명확성/데이터 부족 구분을 추가 검증할 필요가 있다. 내부 thinking에서 원인을 확인한 것으로 표현하지 않는다.
4. **확인된 사실:** 합성 적용 요청을 설명하는 본문은 미실행을 명시하지만 메모리 proposal_status의 ‘적용 요청 기록’ 같은 표현은 요청과 업무 상태를 혼동할 여지가 있다. 이 모호성은 확정적인 ‘적용 완료’ 허위 주장과 구분해 부분 통과로 기록했다. **제안:** 사용자 요청/가정/검증된 수행 상태를 구조적으로 더 분명히 분리하는 후속 검토가 필요하다. 이번에는 구현하지 않았다.

## 한계·재열람

정답지는 미확정 분류체계에 대한 검토용 기준이며 실제 업무 처리 정확도를 검증하지 않는다. 새 표현 전반·다른 모델·다른 추론 설정·실제 센서/문서/DB/예측/승인/설비·물리 IME·중단/장애 주입은 이번 평가에서 검증하지 않았다. 같은 질문 반복은 새 질문 일반화 성능과 다르다. 공유 서버 부하·캐시·독립적인 모델 적재 시간은 통제/분리 계측하지 못했다. 의미 판정은 개발 AI의 한계가 있다.

```bash
cd /home/yt/lam
.venv/bin/python evaluation/compound/score.py
.venv/bin/python evaluation/compound/report.py
```

위 명령은 저장 결과만 읽으며 모델을 호출하지 않는다. run.py는 저장된 요청ID를 재생성하지 않고 원본을 재개하는 보호가 있으며 완료 실험을 삭제/덮어쓰지 않는다. 앱은 기존 http://127.0.0.1:8080 이고 새 기능·서비스·모델은 추가하지 않았다.
'''
 (OUT/'report.md').write_text(report);(OUT/'comparison_report.md').write_text(report)
 write('artifact_hashes.json',{str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and p.name!='artifact_hashes.json'})
 print('Final report:',OUT/'report.md')
if __name__=='__main__':main()
