# 챗봇 v3 — 분석 검증 후 스트리밍

의도 분류체계 2차정리 15개는 미확정 검토안이다. 이 기능은 대화형 의도 분석·매핑·설명이며 실제 업무 답변/검색/DB 조회/예측/계획/승인/설비/트윈 실행이 아니다. 별도 서비스나 프런트엔드 빌드 도구를 추가하지 않았다.

## 실행

```bash
cd /home/yt/lam
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8080
# 이미 실행 중인 transient user service는:
systemctl --user restart food-lam-ui
```

http://127.0.0.1:8080 → 챗봇. VS Code 원격 사용 시8080을 전달한다. 모델은 기존 vLLM http://127.0.0.1:8001/v1 의 qwen3.8:27b만 사용한다. 모델·서버·가중치·GPU 설정은 변경하지 않는다.

## 실제 경로와 호출 수

1. 브라우저가 UUID hex request_id와 사용자 원문을 POST `/api/chat/sessions/{sid}/messages`에 보낸다.
2. 기존 SQLite의 turns에 트랜잭션으로 저장한 뒤202 accepted 응답을 즉시 반환한다. 동일 ID/동일 세션/동일 원문은 같은 레코드를 반환하며 worker를 다시 실행하지 않는다. 다른 원문/세션에 같은 ID를 쓰면409다. 세션의 다른 요청이 처리 중이면409다. ID가 없으면422다. accepted는 대화 메시지 저장 수락이며 실제 업무 접수·승인이 아니다.
3. 단일 worker와 기존 classifier 전역 lock을 사용한다. 분류 테스트와도 모델 호출이 겹치지 않는다. 다른 서비스의 API 작업은 중지하거나 제어하지 않는다.
4. `prompts/chat_analysis_v3.md` + 동일15개 정의 + 제한된 같은 세션 사용자 원문/검증된 해석/활성 슬롯으로 구조화 분석을 한 번 생성한다. 분석에는 reply가 없다. 분석·메모리의 JSON/ID/필드/자료형/출처를 검사하고 저장한다.
5. GET `/api/chat/sessions/{sid}/messages/{rid}/events`는 NDJSON의 snapshot/status/analysis/delta/complete/error/heartbeat를 전달한다. analysis 이벤트는 검증·저장 후 발생한다. 검증 전 본문과 내부 reasoning은 전달하지 않는다.
6. 서버 gate에서 유효 업무/clarify가 있는지 확인한다. 전부 out_of_scope이면 응답 호출0회·빈 reply·별도 상태 문구만 반환한다. 분석 오류는 다른 의도로 바꾸지 않는다. 복합 질문의 범위 밖 항목은 응답 모델 입력에서 제외한다.
7. 유효/clarify는 `prompts/chat_response_v3.md`와 검증된 요청 해석·활성 메모리·모의 도구 후보만으로 vLLM `/chat/completions`를 stream=true로 한 번 호출한다. 원래 복합 질문 전체나 원시 대화는 응답 모델에 재전달하지 않는다. 응답은 실제 vLLM SSE content delta를 NDJSON delta로 전달한다. 완료문을 자르거나 타이머로 연출하지 않는다. 분리된 reasoning 필드는 무시하고 본문 채널의 `<think>` 표식은 오류로 중단한다. 표식 분할 유출을 막기 위한 최대9문자 버퍼만 둔다.

따라서 정상 업무·clarify는 분석1+응답1=최대2회, 범위 밖만 있거나 분석 검증 실패는 응답0회다. 중단 시 응답을 아직 시작하지 않았다면 분석1회만 남을 수 있다. 자동 재시도·다른 모델 대체·답변/ID 보정은 없다. 의도 ID가 맞다는 사실만으로 설명의 의미 정확성을 보장하지 않는다.

설정: temperature=0, seed=20261012, enable_thinking=false. 분석 출력 한도4096, 새 응답 출력 한도1536. 분류 전용 테스트는 기존256/배열 계약·프롬프트를 유지한다. `backend/chatbot.py::chat`는 과거 v2 평가용 진입점으로 남겼고 새 웹 경로는 사용하지 않는다.

## 입력과 세션 UI

Enter는 form.requestSubmit()을 통해 버튼과 같은 경로를 사용하고 Shift+Enter는 줄바꿈한다. isComposing/조합 이벤트/keyCode229와 조합 종료 직후 Enter를 확인한다. 공백과 중복 제출은 막는다.

입력은 accepted를 받았을 때 전송 당시 초안이 그대로인 경우에만 비운다. 완료 시에는 절대 비우지 않는다. 처리 중 다음 질문을 작성할 수 있다. 실패 시 원문·요청 ID outbox를 localStorage에 남겨 “전송 확인·복구”로 같은 ID를 사용한다. 모델 오류를 다시 생성하는 버튼은 없다. 오류 원문을 입력창으로 복사한 뒤 사용자가 별도의 새 메시지를 보낼 수 있다.

세션 목록은 서버 SQLite에서 읽고50개씩 더 볼 수 있다. 선택 세션/초안/outbox는 브라우저 저장소에서 구분한다. 다른 세션으로 이동하면 기존 응답 연결을 닫아 중단시키고, view token+session_id+request_id+seq 검사로 이전 조각을 배제한다. fetch 스트림은 TextDecoder(stream=true)로 UTF-8을 처리한다. 모든 사용자·모델 출력은 textContent로 표시한다.

## 메모리

`backend/chat_analysis.py`의 버전 관리되는 슬롯 계약을 사용한다. 모델이 new_task/continue/correct/withdraw와 updates/remove를 같은 분석 호출에서 생성한다. 코드가 질문 키워드로 분류하거나 수량을 교정하지 않는다.

동일 slot의 update는 과거 값을 대체하고 remove는 철회하며 new_task는 기존 슬롯을 상속하지 않는다. 변경 근거는 현재 사용자 턴을 반드시 포함하고 이전 내용을 보완하면 그 사용자 원문도 연결한다. 원문 인용은 정확한 연속 부분 문자열로, value/대상/기간/조건은 정리한 해석으로 검사한다. 슬롯 이름의 의미 일치나 해석 자체는 모델 오류가 남을 수 있으며 문자열 검사가 의미 정답을 인증하지 않는다.

최근 최대8개 교환/기존12,000자 예산의 사용자 맥락을 선택한다. 검증된 이전 해석 메타데이터는 별도최대4,000자, 활성 슬롯은 별도최대4,000자다. 전달 범위의 실제 사용자 출처가 전부 없는 슬롯은 제외한다. 선택·제외·한도를 record.context_limits에 기록하며 빠진 사실을 코드가 만들어 보완하지 않는다. 과거 AI 설명은 실제 조회 결과나 제안이 아니고 다음 모델의 확인 사실로 전달하지 않는다. 실제 업무 수행 여부는 별도의 false 메타데이터와 UI 상세로 구분한다.

## 저장·중단·복원

같은 data/chatbot/sessions.sqlite3의 sessions/turns를 재사용한다. 새 레코드에는 message_id/order, 수락 시각, 단계 상태, 원본 분석과 검증, 활성·이전 슬롯, 요청별 상태, 모의 도구 버전, 모델/프롬프트/스키마/시간/토큰을 추가한다. 기존 레코드를 변경하거나 새 계약으로 변환하지 않는다.

응답은 약0.35초 또는128문자 증가마다 부분 저장하고 완료/오류/중단 때 최종 저장한다. 원시 네트워크 조각을 영구 저장하는 대신 수신한 원문 응답·실제 보인 부분을 구분한다. 갑작스러운 강제 종료 직전의 최대 버퍼 구간은 아직 저장되지 않았을 수 있다. 연결 종료는 해당 요청의 취소 신호를 설정하며 다른 요청을 중단하지 않는다. 분석 중 취소는 현재 분석 HTTP가 반환될 때 인지될 수 있으나 두 번째 호출은 하지 않는다. 응답 중 취소는 다음 실제 조각을 읽을 때 인지하고 연결을 닫는다.

서버 재시작 때 남은 pending은 interrupted로 복구하고 부분 답변을 유지하며 자동 재생성하지 않는다. 완료한 저장 대화는 GET만으로 연다. 이벤트 ring buffer는 메모리에만 bounded512개이며 재연결에는 권위 있는 저장/현재 snapshot을 먼저 전달한다. snapshot 이전 조각을 재생해 중복 붙이지 않는다. 브라우저 연결이 닫힌 요청은 재생성이 아니라 저장된 종료 상태로 확인한다.

시간은 대기/분석/응답/첫 본문/전체를 구분한다. 토큰과 HTTP 시간은 제공된 측정만 기록하며 중단되어 응답 usage를 받지 못하면 미측정이다. 초기 검증 원본의 루트 model_http_seconds는 분석 단계 호환 필드였으므로 보고서는 각 stage의 실제 HTTP 시간을 사용한다. 최종 코드에서는 제공된 분석·응답 HTTP 시간의 합(미수신 단계가 있으면 null)을 기록한다.

## 모의 도구·내보내기

`settings/mock_tools.json`은 참고자료의 필요한 도구 기능명을 통합한17개 모의 항목이다. ID·설명·모의 입력 스키마·미구현·실행/쓰기 비활성·승인 필요/미승인을 기록한다. 이 입력 스키마는 실제 어댑터 계약이 아니라 모의 검토안이다. intent_tools에서 의도별 연결 후보를 편집할 수 있다. `backend/mock_catalog.py`는 메타데이터를 읽기만 하며 실행 함수가 없다. 이번에도 실제/native tool calling, DB·VDB·GraphDB·모델·설비 어댑터는 없다. 66개 의도 정의/질문/번호를 분류 프롬프트에 넣지 않는다.

GET `/api/chat/sessions/{sid}/export`는 JSONL을 내려준다. 원본 ID/원문/분석/응답/버전/검증/mock 정보를 포함하고 model_labels는 모델 출력이며 labels_are_gold=false, review_status=unreviewed, human_label=null이다. 인증 필드와 내부 reasoning 필드는 제외한다. 자동 학습·골든셋 등록은 없다. 기존 기록에 없던 message_id/schema/mock 필드는 null/legacy로 구분한다. DB·감사·내보낸 검증 파일은 기존 .gitignore의 data/ 및 evaluation/results/ 아래에 저장한다.

## 검사와 한계

2026-11-02 기능 회귀 수정: 현재 분석 프롬프트/검증 계약은 `chat-analysis-v3.0.1` / `chat-stream-contract-v3.0.1`, 메모리는 `user-context-slots-v1.0.1`이다. 출력 JSON 필드와 출력 한도는 바꾸지 않았다. new_task는 기존 활성 슬롯을 전부 초기화하며 실제 기존 슬롯을 현재 사용자 근거로 remove하는 명시적 해제도 허용한다. 이전 턴을 requests/updates에 상속하거나 존재하지 않는 슬롯을 해제하는 경우는 계속 거부한다. 실패한 새 분석은 활성 메모리로 커밋하지 않는다.

같은 세션에 참조 대상 근거가 없으면 clarify로 확인하고, 업무 요청에 붙은 분류 설명/표시 지시는 별도 업무로 분리하지 않도록 분석 프롬프트를 구분했다. 이 의미 판정은 모델이 하며 키워드나 특정 질문으로 의도를 교정하지 않는다. 실제 비제조 업무가 섞인 경우와 업무 없는 ID 강요는 기존 범위 밖 정책을 유지한다.

분석 실패의 model_output/parsed_analysis/validation_failure는 미검증 감사 자료로 저장한다. 진행 중에는 노출하지 않고 오류 종료 후 접힌 상세와 export에서만 제공한다. 예전 오류 행도 조회 시 같은 공통 함수를 이용해 감사 자료를 구성하며 DB 원문/오류 상태는 덮어쓰지 않는다. 별도 reasoning 채널·인증 필드는 표시하지 않는다. 분석/응답/첫 실제 본문 조각/전체 시간은 기존 측정을 유지하고 heartbeat/status는 첫 본문으로 집계하지 않는다.

이번 검증 자료는 `evaluation/results/chat-fixes-20261102-01/`에 별도로 보존한다. 실제 후속 질문에서 `history.query` 대신 `status.query`를 반환한 사례는 미해결 오분류로 남겼다. 이전 실험 수치와 결과는 변경하지 않았다.

```bash
.venv/bin/pytest -q
# 모델 재호출 없는 저장 결과 집계
.venv/bin/python evaluation/stream/report.py
```

평가 원본·고정 질문·스크린샷·복원은 evaluation/results/chatbot-stream-20261102-01/를 확인한다. 실제 모델 대표 사례는 소표본이며 전체15개 의도의 성능을 새로 확정하지 않는다. 분석/응답의 의미는 자동 출처 검사와 별도다. 물리 OS IME, 다중 Uvicorn worker, 인증/사용자 권한, 실제 설비/DB/학습은 이번 범위에 없다. 현재 단일 Uvicorn worker로 실행한다.
