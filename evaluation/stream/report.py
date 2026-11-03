"""Read saved evidence only: never import/invoke the chat/model executor."""
import csv
import difflib
import hashlib
import json
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'evaluation/results/chatbot-stream-20261102-01'


def read(name):
    return json.loads((OUT / name).read_text())


def rows(name):
    return [json.loads(line) for line in (OUT / name).read_text().splitlines() if line]


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * p / 100
    lo = int(k)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (k - lo)


def stats(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return {'n': len(values), 'p50': percentile(values, 50), 'p95': percentile(values, 95),
            'min': min(values) if values else None, 'max': max(values) if values else None}


def csvfile(name, records):
    if not records:
        return
    with (OUT / name).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(records[0]))
        w.writeheader()
        w.writerows(records)


def preservation():
    old = read('preservation_before.json')
    changed = [p for p, h in old['files'].items() if not (ROOT / p).exists() or sha(ROOT / p) != h]
    with sqlite3.connect(ROOT / 'data/chatbot/sessions.sqlite3') as c:
        stored = {r[0]: hashlib.sha256(json.dumps(r, ensure_ascii=False).encode()).hexdigest()
                  for r in c.execute('SELECT * FROM turns')}
    altered = [rid for rid, h in old['turns'].items() if stored.get(rid) != h]
    with (ROOT / 'data/chatbot/requests.jsonl').open('rb') as f:
        prefix = hashlib.sha256(f.read(old['log_bytes'])).hexdigest()
    result = {'protected_files': len(old['files']), 'modified_protected_files': changed,
              'historical_turns': len(old['turns']), 'modified_historical_turns': altered,
              'original_log_prefix_bytes': old['log_bytes'], 'original_log_prefix_unchanged': prefix == old['log_sha256']}
    write('preservation_after.json', result)
    assert not changed and not altered and result['original_log_prefix_unchanged'], result
    return result


def snapshots():
    files = [p for folder in ['backend', 'frontend', 'prompts', 'settings', 'docs', 'tests', 'evaluation/stream']
             for p in (ROOT / folder).rglob('*')
             if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py', '.js', '.html', '.css', '.md', '.json'}]
    files += [ROOT / 'README.md', ROOT / '.gitignore']
    changed, diff = [], []
    for path in sorted(files):
        relative = path.relative_to(ROOT)
        before = OUT / 'before' / relative
        content = path.read_text()
        if before.exists() and before.read_text() == content:
            continue
        after = OUT / 'after' / relative
        after.parent.mkdir(parents=True, exist_ok=True)
        after.write_text(content)
        changed.append({'path': str(relative), 'kind': 'modified' if before.exists() else 'added',
                        'before_sha256': sha(before) if before.exists() else None, 'after_sha256': sha(path)})
        diff.extend(difflib.unified_diff(before.read_text().splitlines(True) if before.exists() else [],
                                         content.splitlines(True), fromfile='before/' + str(relative), tofile='after/' + str(relative)))
    write('changed_files.json', changed)
    (OUT / 'implementation.diff').write_text(''.join(diff))
    initial = read('manifest.json')['hashes']
    write('final_code_hashes.json', {str(p.relative_to(ROOT)): sha(p) for p in files})
    write('post_measurement_changes.json', [
        {'path': name, 'measurement_sha256': value, 'final_sha256': sha(ROOT / name)}
        for name, value in initial.items() if (ROOT / name).exists() and sha(ROOT / name) != value])
    return changed


def main():
    main_runs = rows('runs.jsonl')
    reviews = read('semantic_reviews.json')
    lookup = {r['request_id']: r for r in reviews}
    samples = [(f"{r['conversation']}/{r['turn']}", r['record'], r) for r in main_runs]
    browser = rows('browser_real_runs.jsonl')[0]
    samples += [('browser_valid', browser['record'], browser)]
    partial = read('browser_interrupt_run.json')
    harness = read('browser_harness_interrupted_record.json')
    samples += [('browser_intentional_cancel', partial['record'], partial), ('browser_harness_disconnect', harness['record'], harness)]
    metrics, language = [], []
    for label, r, wrapper in samples:
        a, b = r.get('analysis_stage') or {}, r.get('response_stage') or {}
        usage = b.get('usage') or {}
        t = r['timings']
        review = lookup.get(r['request_id'])
        # The interrupted analyses/partial response were read as well. Missing final body is not scored as a complete answer.
        language.append({'case': label, 'request_id': r['request_id'], 'status': r['status'],
                         'reviewed_fields': '원본 분석의 목적·이유·질문·정규화 값; 존재하는 사용자용 본문 전체',
                         'automatic_detection': '문자 검출만으로 판정하지 않음', 'judgment': '이 표본에서 실제 언어 혼입 관찰되지 않음',
                         'allowed_expressions': '의도 ID·SOP·API·mock·slot 식별자 등 기술 표기',
                         'reviewer': '개발 AI 원문 검토; 독립/블라인드/사람 검토 아님'})
        metrics.append({'case': label, 'request_id': r['request_id'], 'session_id': r['session_id'],
                        'status': r['status'], 'id_correct': wrapper.get('id_correct'),
                        'semantic_explanation': review['explanation'] if review else '별도 UI 표본/완료 의미 점수 미집계',
                        'calls': r['llm_call_count'], 'analysis_calls': r['analysis_call_count'], 'response_calls': r['response_call_count'],
                        'analysis_validated': r['analysis_validated'], 'actual_tool_calls': r['actual_tool_calls'],
                        'queue_seconds': t.get('queue_seconds'), 'analysis_seconds': t.get('analysis_seconds'),
                        'response_seconds': t.get('response_seconds'), 'first_fragment_seconds': t.get('first_fragment_seconds'),
                        'total_seconds': t.get('total_seconds'), 'client_total_seconds': wrapper.get('client_total_seconds'),
                        'browser_total_dom_seconds': wrapper.get('browser_total_dom_seconds'),
                        'analysis_http_seconds': a.get('model_http_seconds'), 'response_http_seconds': b.get('model_http_seconds'),
                        'analysis_input_tokens': a.get('input_tokens'), 'analysis_output_tokens': a.get('output_tokens'),
                        'response_input_tokens': usage.get('prompt_tokens'), 'response_output_tokens': usage.get('completion_tokens'),
                        'error_kind': r.get('error_kind'), 'reply_characters': len(r.get('reply') or '')})
    csvfile('metrics.csv', metrics)
    csvfile('scored.csv', [{k: v for k, v in r.items()} for r in reviews])
    csvfile('language_review.csv', language)
    group_metrics = {}
    for group, selected in [('main_completed_14', metrics[:14]), ('completed_with_body_13', [r for r in metrics if r['status'] == 'completed' and r['response_calls']]),
                            ('out_of_scope_no_response_2', [r for r in metrics if r['status'] == 'completed' and not r['response_calls']]),
                            ('interrupted_2', [r for r in metrics if r['status'] == 'interrupted'])]:
        group_metrics[group] = {key: stats([r.get(key) for r in selected]) for key in
                               ['total_seconds', 'queue_seconds', 'analysis_seconds', 'response_seconds', 'first_fragment_seconds', 'analysis_http_seconds', 'response_http_seconds']}
    summary = {'main_cases': 14, 'main_intent_set_exact': sum(r['id_correct'] for r in main_runs),
               'main_explanation_pass': sum(r['explanation'] == '통과' for r in reviews),
               'main_explanation_partial': sum(r['explanation'] == '부분 통과' for r in reviews),
               'actual_requests_including_browser_and_interruptions': len(samples),
               'generation_posts': sum(r['calls'] for r in metrics),
               'completed': sum(r['status'] == 'completed' for r in metrics),
               'interrupted': sum(r['status'] == 'interrupted' for r in metrics),
               'format_or_source_errors': sum(r['status'] == 'error' for r in metrics),
               'main_validated_before_body': sum(r['validated_before_body'] for r in main_runs),
               'actual_tool_calls': sum(r['actual_tool_calls'] for r in metrics),
               'observed_language_mixing': 0, 'language_reviewed_responses': len(samples),
               'latency': group_metrics,
               'usage': {key: {'sum_known': sum(r[key] for r in metrics if isinstance(r[key], int)), 'known_n': sum(isinstance(r[key], int) for r in metrics), 'total_n': len(metrics)}
                         for key in ['analysis_input_tokens', 'analysis_output_tokens', 'response_input_tokens', 'response_output_tokens']},
               'conditions': '캐시 초기화·별도 워밍업 없음. 이미 실행 중인 공유 vLLM, 자체 순차 요청. 소표본·단회 대표 검증; 전체 의도 정확도/안정적인 p95 아님.'}
    write('summary.json', summary)
    write('failures.json', {'semantic_partial': [r for r in reviews if r['explanation'] != '통과'],
                           'interrupted': [r for r in metrics if r['status'] == 'interrupted'],
                           'incidents': read('regression_incidents.json')})
    preserved = preservation()
    changed = snapshots()
    def fmt(v):
        return '미측정' if v is None else f'{v:.3f}'
    table = '\n'.join(f"| {name} | {m['total_seconds']['n']} | {fmt(m['total_seconds']['p50'])} | {fmt(m['total_seconds']['p95'])} | {fmt(m['analysis_seconds']['p50'])} | {fmt(m['response_seconds']['p50'])} |" for name, m in group_metrics.items())
    failure_text = '\n'.join(f"- {r['conversation']}/{r['turn']} — {r['reason']} (`{r['request_id']}`)" for r in reviews if r['explanation'] != '통과')
    report = f'''# 챗봇 분석 검증 후 스트리밍·모의 도구·세션 저장 구현 확인

분류체계 2차정리는 미확정 검토안이다. 실제 업무 답변·검색·도구 실행·예측·승인·설비 제어는 추가하지 않았다.

## 결과

| 항목 | 실제 확인 |
| --- | --- |
| 고정 대표 질문 | 7개 대화·14개 메시지, 기대 의도 집합 14/14 일치 |
| 분석 검증 전 본문 미노출 | 14/14 |
| 범위 밖만인 요청 | 2/2, 응답 생성 0회·AI 본문 없음 |
| 전체 실제 요청 | 17건, 완료15·중단2; 생성 API 총31회 |
| 완료 설명의 의미 검토(주14건) | 통과11·부분 통과3; ID 정확도와 별도 |
| 실제 업무 도구 호출 | 0회 |
| 언어 | 17건의 존재하는 분석/본문에서 혼입 관찰되지 않음; 완성 본문13·부분 본문1·본문 없는3 |
| 자동 검사 | pytest78 통과; 브라우저 입력 fixture13조건·실제 중단 표본16조건·읽기 전용8조건 통과 |

의도 점수는 새 소표본의 집합 비교이며 전체15개 의도 성능을 확정한 수치가 아니다. 개발 AI가 전체 원문을 읽은 의미/언어 검토이며 독립 AI·블라인드·사람 검토로 표현하지 않는다. 실제 새 검증에서 case.search/data.analyze/whatif.simulate/process.status/dt.update는 평가하지 않았다.

## 처리 흐름·버전

브라우저 UUID request_id → SQLite 원문 수락202 → 입력 비우기 → 분석1회 → JSON/ID/출처 검증·저장 → 서버 gate → 유효/clarify만 진짜 vLLM 스트림1회 → 부분/종료 저장. 분석 오류는 오류로 끝나며 범위 밖/clarify로 바꾸지 않는다. 복합 중 범위 밖 부분은 응답 모델 입력에서 제외한다.

- 분석/응답 프롬프트: prompts/chat_analysis_v3.md (`chat-analysis-v3.0.0`), prompts/chat_response_v3.md (`chat-response-v3.0.0`). 분석에 reply 필드는 없다.
- 스키마/계약: chat-stream-contract-v3.0.0, memory user-context-slots-v1.0.0. 기존 분류 전용256토큰/배열 계약·프롬프트·15개 정의 보존.
- vLLM0.32.0, qwen3.8:27b, 기존 Qwen3.8-27B-FP8. image/digest/실행명령/장치/버전은 manifest.json. enable_thinking=false, temperature0, seed20261012. 분석4096/응답1536 출력 한도. 가중치·엔진·GPU 설정 변경 없음.
- 정상/clarify 2회, 범위 밖만/분석 오류 응답0회. 자동 재시도·정답 보정·답변 재작성·다른 모델 대체 없음. /models·세션GET·폴링·보고서 열람은 생성 호출이 아니다.
- 최종 측정 후 소규모 하드닝(취소 경합/메타데이터/누락 시간 표기 등)은 post_measurement_changes.json·regression_incidents.json에 구분했다. 최종 단위/fixture/읽기 검사는 다시 실행했다. 측정 당시 원본은 그대로이며 전체 실제 모델14건을 최종 코드로 재측정했다고 주장하지 않는다. 프롬프트·스키마·카탈로그는 측정 중/후 동일하다.

## 입력·스트림·상태

Enter/버튼은 같은 submit 함수, Shift+Enter 줄바꿈, isComposing·composition 이벤트·keyCode229·조합 종료 보호를 사용한다. 저장 수락 시 전송 당시 초안만 비우며 응답 완료 때 다음 초안을 지우지 않는다. 원문/ID outbox로 수락 여부 불명 시 같은 ID 확인·복구가 가능하다. 동일 ID/세션/원문은 기존 행을 재사용하고 다른 원문/세션은409다.

진짜 vLLM SSE의 content 조각을 NDJSON status/analysis/delta/complete/error로 전달한다. 완성문 시간 분할은 없다. 검증되지 않은 JSON·reasoning은 노출하지 않는다. UTF-8 TextDecoder(stream=true), 세션/요청/view/sequence 검사와 textContent 렌더링을 쓴다. split think 표식 보호용9문자 꼬리 버퍼만 둔다.

연결 종료/취소는 해당 요청만 중단하며 부분 본문·상태·원문을 보존한다. 분석 HTTP는 반환할 때 취소를 인지할 수 있고 응답은 다음 조각 수신 때 중단한다. 서버 재시작 후 pending은 interrupted로 복구한다. 물리 OS 한글 IME는 자동화로 재현하지 못했고 합성 composition 이벤트만 검사했다. 실제 엔진 장애/무응답 타임아웃 주입은 하지 않았으며 단위검사로 오류·잘림·출처 실패를 확인했다.

## 저장·메모리·모의 도구

data/chatbot/sessions.sqlite3 기존 sessions/turns를 확장 사용한다. 원문·실제ID·시각·단계·분석 원문·검증·모델/계약/프롬프트 버전·시간·토큰·부분/최종 답변을 저장한다. 원본 과거 기록은 새 형식으로 변환하지 않는다. 세션 목록/새 대화/기록 복원/내보내기를 기존 UI에 연결했다.

최근8개 교환·사용자 맥락12000자, 이전 해석4000자·활성 슬롯4000자 한도를 기록한다. 같은 slot 최신값 대체/철회/new_task 초기화로 갱신하고 정확한 현재/이전 사용자 quote/turn_id를 연결한다. 정리한 해석은 부분 문자열 일치를 요구하지 않는다. 모델의 의미 슬롯 식별·해석은 여전히 오류 가능하며 자동 의미 검증이 아니다. 실제 사례에서4대→2대, 작업자3명 철회, 별도 세척 질문의 맥락 초기화가 확인됐다.

settings/mock_tools.json의17항목은 참고자료의 도구 기능명을 통합한 모의 카탈로그다. ID/설명/입력 형식/실제 미구현/실행·쓰기 비활성/승인 필요·미승인과 의도별 후보를 기록한다. 입력 스키마는 실제 어댑터가 아닌 검토용 가정이다. 기존 실제 실행 카탈로그는 없었다. backend/mock_catalog.py는 메타데이터만 읽으며 실행기가 없다. 66개 의도 자료/정답표를 모델 입력에 추가하지 않았다.

export JSONL은 원문·분석·답변·버전·검증·mock·원본ID와 model_labels를 포함한다. labels_are_gold=false, review_status=unreviewed, human_label=null. 자동 학습/골든셋 등록 없음. 인증 정보·reasoning 제외. data/와 evaluation/results/는 Git 제외다.

## 실제 검증과 남은 설명 오류

{failure_text}

위3건을 자동 고치거나 통과에 합치지 않았다. 요청/전제를 수행 사실로 오인하는 표현은 프롬프트만으로 완전히 차단되지 않는다. 코드의 출처 검사는 인용 존재를 확인하며 주장 의미를 인증하지 않는다.

브라우저: 정상 응답의 첫 본문 관측 {browser['browser_first_body_seconds']:.3f}초, 최종 DOM 관측 {browser['browser_total_dom_seconds']:.3f}초. 관측 방법/시간은 browser_real_runs.jsonl에 있고 정확한 화면 첫 페인트 측정은 아니다. 별도 실제 스트림을 첫 조각 후 취소해 ‘현재 유량’ 부분 응답·interrupted를 새로고침/재시작/내보내기에서 보존했다. browser_interrupt_run.json의16조건 통과. 브라우저 첫 스크립트는 reload 후 observer 미재설치로 실패했고 그때의 분석1회/응답0회 기록도 browser_harness_interrupted_record.json·browser_initial_harness_error.log에 보존했다. 해당 요청을 재생성하지 않고 사전 고정한 별도 질문으로 중단 흐름을 확인했다. 테스트 harness 실패와 모델 오류를 구분한다.

입력 fixture는 실제 모델 호출 없는 UI 계약 검사이며 실제 스트리밍 성능으로 계산하지 않는다. 완료 원문/오류·부정 케이스의 서버검증/세션 격리/중복ID/재시작/UTF-8/추론 차단/내보내기는 자동검사에서 확인했다. 강제 종료 순간의 미저장 버퍼/다중 worker/인증·권한은 미검증이다. 현재는 단일 worker다.

## 실측 시간(초)

| 집단 | n | 전체 p50 | 전체 p95 | 분석 p50 | 응답 p50 |
| --- | --- | --- | --- | --- | --- |
{table}

전체는 저장 수락→서버 종료이며 대기·분석·응답을 포함한다. 클라이언트 전체·UI 관측은 metrics.csv에서 별도 구분한다. 중단2건은 정상 완료 통계와 합치지 않았다. 범위 밖 짧은 종료도 본문 완료13건과 분리했다. 소표본 p95는 안정적 운영 성능이 아니다. 별도 워밍업/캐시 초기화/로드 실험은 하지 않았고 공유 서버의 다른 작업을 종료하지 않았다. 기존 한 번 호출 실험과 직접 속도 개선으로 비교하지 않는다.

단계별 토큰은 summary.json·metrics.csv. 중단된 응답은 usage 미수신이므로 공백/null이며0을 채우지 않았다. vLLM이 제공하지 않은 Ollama load/prompt/eval duration은 미측정이다. 초기 기록의 루트 model_http_seconds는 분석 호환 필드이므로 보고서는 원본 analysis_stage/response_stage HTTP 측정을 사용한다. 최종 코드는 알려진 단계 합(미수신 단계 있으면null)으로 기록한다. 기존 기록은 수정하지 않았다.

Docker 접근 로그(server_access_audit.json)의 고정 측정 구간에서도 생성 POST31개가 확인돼 자체 기록31회와 수가 일치했다. 추가 POST는 관찰되지 않았다. 접근 로그에 요청 ID가 없어 각 호출의 소유자를 독립적으로 확정한 것은 아니다. 다른 사용자 작업을 종료하지 않았다.

## 변경·원본 보존·재실행

주요 구현은 backend/chat_flow.py·chat_analysis.py·chat_flow_schema.py·mock_catalog.py, 기존 main.py·llm_client.py·chatbot.py와 frontend/chatbot.js·index.html·app.css다. 상세 변경 파일·전후 SHA는 changed_files.json, before/after, implementation.diff에 있다. 신규 설정/프롬프트·문서·검사/평가 스크립트 포함 총 {len(changed)}파일이다.

기존 보호 파일 {preserved['protected_files']}개·기존 DB turn {preserved['historical_turns']}개·기존 감사 JSONL 앞 {preserved['original_log_prefix_bytes']}바이트의 해시가 모두 유지됐다(preservation_after.json). 질문/정답/모델 원문·실패 기록을 고치지 않았다.

```bash
cd /home/yt/lam
.venv/bin/pytest -q
.venv/bin/python evaluation/stream/report.py   # 저장 자료만 집계, 모델 호출 없음
.venv/bin/python evaluation/stream/read_only_verify.py  # 완료 기록 재사용·조회만
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8080
```

접속 http://127.0.0.1:8080 (원격 VS Code는8080 포트 전달). 현재 food-lam-ui user service 실행. 실제 모델 재평가 run.py는 동일 실험의 입력 해시가 변경되면 중단하도록 했으며 새 실험 디렉터리/고정값을 분리해야 한다. 기존 결과를 덮어써 재실행하지 않는다.

복원은 rollback.md를 따른다. 모델/DB/과거 평가 기록은 복원 과정에서 삭제하지 않는다. 기존 분류 테스트는 같은 탭·API·출력 계약을 유지하며 새 챗봇의 메모리/스트리밍을 주입하지 않는다.
'''
    (OUT / 'report.md').write_text(report)
    (OUT / 'comparison_report.md').write_text(report)
    (OUT / 'rollback.md').write_text('''# 기존 v2 코드로 복원

현재 처리 중인 요청이 없는지 확인하고 웹 서비스만 중지한다. vLLM·Ollama·모델 가중치·SQLite·감사 기록·평가 기록은 삭제/이동/복원하지 않는다.

```bash
cd /home/yt/lam
systemctl --user stop food-lam-ui
.venv/bin/python - <<'PY'
import json,shutil
from pathlib import Path
root=Path('/home/yt/lam'); out=root/'evaluation/results/chatbot-stream-20261102-01'
for item in json.loads((out/'changed_files.json').read_text()):
    old=out/'before'/item['path']
    if old.is_file(): shutil.copy2(old,root/item['path'])
# 추가 모듈/프롬프트는 사용되지 않으므로 삭제하지 않아도 된다.
PY
systemd-run --user --unit=food-lam-ui --property=WorkingDirectory=/home/yt/lam /home/yt/lam/.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8080
```

중지한 transient unit은 사라질 수 있으므로 systemctl start 대신 원래 실행 명령을 사용한다. 기존 UI는 새 v3 중단/모의 도구 상태를 전부 표현하지 못할 수 있다. 새 기록은 원본 JSONL/SQLite 또는 v3 코드로 열람하고 과거 계약으로 변환하지 않는다.
''')
    write('artifact_hashes.json', {str(p.relative_to(OUT)): sha(p) for p in OUT.rglob('*') if p.is_file() and p.name != 'artifact_hashes.json'})
    print(json.dumps({'requests': len(samples), 'generation_calls': summary['generation_posts'], 'main_exact': summary['main_intent_set_exact'], 'preservation': preserved, 'report': str(OUT / 'report.md')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
