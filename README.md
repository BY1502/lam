# 식품 제조 LAM — 의도 분류체계 2차정리 · 검토안

분류 테스트는 질문을 분류하고, 챗봇은 요청 목적을 분석·분류해 설명한다. 실제 답변·검색·계산·예측·계획·도구 실행·설비 제어는 하지 않는다. 15개 의도와 복합 요청 정책은 미확정이며, 이번 평가로 확정하지 않는다.

## 실행

```bash
cd /home/yt/lam
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8080
```

브라우저: http://127.0.0.1:8080 (VS Code 원격 사용 시 8080 포트 전달).
현재 사용자 서비스 `food-lam-ui.service`로 실행하며 `systemctl --user restart food-lam-ui`로 재시작할 수 있다. transient 서비스이므로 재부팅 후에는 위 실행 명령을 사용한다.

기본 연결은 기존 vLLM `http://127.0.0.1:8001/v1`, served model `qwen3.8:27b`다. `VLLM_URL`, `VLLM_MODEL`, `VLLM_API_KEY` 환경변수로 지정한다. 기존 Ollama 연결 설정 코드는 보존했지만 이번 평가는 vLLM만 사용한다. UI는 엔진을 켜거나 바꾸지 않는다. Ollama 컨테이너는 중지 상태를 유지한다. 서로 다른 모델/엔진을 동시에 메모리에 적재하지 않는다.

## 처리 경로

`POST /api/classify → backend/classifier.py → backend/llm_client.py → 생성 POST 1회 → 엄격한 JSON 검증 → 서버 정의표의 한글 명칭 표시`

- system: `prompts/classification_v2.md` 규칙 + `taxonomy/intents_v2.json`의 15개 정의
- user: 질문 원문만. 이전 대화·선택 맥락·정답·평가 질문 목록은 전달하지 않는다.
- 출력: `{"intents":["status.query"]}`. 허용 ID의 비어 있지 않은 배열. 중복 불가, 등장 순서, 임의의 최대 2개 제한 없음.
- 추가 필드·알 수 없는 ID·빈 배열·중복·잘림·빈 출력·API 오류는 `classification_error`. 보정·자동 재시도·대체 모델 호출 없음.
- vLLM 구조화 출력은 `uniqueItems`를 거부하므로 해당 스키마 키는 전송하지 않는다. 중복은 Python 검증에서 오류로 처리하며 자동 제거하지 않는다.
- 실제 모델 원문·전송 메시지·시간·토큰은 `data/runtime/requests-v2.jsonl`에 따로 저장한다.
- 정의 버전: `food-manufacturing-second-review-draft-v2.0.0`
- 프롬프트 버전: `classification-only-second-review-v2.0.0`
- 첨부 원문: `taxonomy/second_review_source.txt`. 정의 문장/문장부호/내부 줄바꿈을 바꾸지 않았다. `intents_v2.json`은 원문 문단 사이 구분용 빈 줄만 JSON 구조로 옮긴다.

## 재현 평가

```bash
cd /home/yt/lam
.venv/bin/python -m pytest -q
.venv/bin/python -m evaluation.run_second_review --experiment second-review-<새ID>
# 저장 결과만 다시 집계 (모델 호출 없음)
.venv/bin/python -m evaluation.run_second_review --experiment second-review-<ID> --score-only
```

새 데이터는 `evaluation/datasets/second_review/`에 있다. 단일 75개, 명확한 경계 54개(13경계 각2쌍 이상), 복합 12개, 규칙 무시 12개, 정답 미확정 경계 검토 5개. 총 158문항을 각3회 실행한다. 일반 정확도 대상은 고유153개×3회=459건이다. 미확정5개×3회=15건은 정답을 확정하지 않고 별도 관찰한다. 질문·정답·근거·허용 기준은 실행 전에 해시 고정한다.

설정은 기존 vLLM Qwen, temperature=0, seed=20261012, thinking 비활성, 출력 한도256 그대로다. 모델 토크나이저로 15개 ID 전체 출력이 70토큰임을 사전 확인했다. 각 반복의 준비 요청을 점수/지연에서 제외한다. 질문 순서는 manifest에 기록한다. prefix cache는 초기화하지 않는다. 평가 lock을 통해 UI의 새 추론을 차단한다. 중단 시 같은 실험ID로 재개하며 성공·실패 모두 이미 기록된 항목은 다시 호출하지 않는다. 코드/입력 해시가 바뀌면 재개하지 않는다. 모델 설정/서비스를 전환하지 않는다.

`evaluation/results/<ID>/`에 manifest, questions_gold, 정의·프롬프트·정책 사본, runs.jsonl, warmups.jsonl, scored.csv, metrics.csv, summary.json, failures.json, confusion_matrix.csv, comparison_report.md를 저장한다. 준비 실패 실험도 삭제하지 않는다. 모델이 제공하지 않는 시간/토큰 지표는 추정하지 않는다. 형식 정상과 의미 정답은 별개이며 합성셋 성적이 현장 일반화나 실제 업무 답변 성능을 보장하지 않는다.

## 1차 자료 보존과 복원

기존 `taxonomy/intents.json`, `taxonomy/intent_classification.md`, `prompts/classification.md`, `evaluation/datasets/basic.json` 등과 과거 결과 디렉터리는 보존한다. 2차 분류·채점에서는 읽지 않는다. UI 저장 목록은 현재 분류체계 버전만 표시하며 보고서를 열어도 모델을 재호출하지 않는다. `evaluation/run_classification.py`는 1차 버전에서만 실행되도록 보호한다.

수정 전 코드·UI·테스트·README는 `data/archive/first-review-before-v2/`에 보존했다. 복원하려면 **평가가 종료된 상태에서** 웹 서버를 중지하고 해당 폴더의 원래 상대 경로 파일을 프로젝트에 복사한 뒤 다시 시작한다. 이때 원래 1차 정의/프롬프트가 다시 선택된다. 2차 실험 결과와 가중치는 삭제할 필요가 없다. 기존 평가의 재집계는 변경된 입력 해시 때문에 기존 실행기에서 거부될 수 있으므로 원본 보고서를 그대로 조회하거나 당시 코드를 복원한 상태에서 수행한다.


## 챗봇 탭 — 대화형 의도 분석·설명

상단 탭에서 **챗봇**을 선택한다. 기존 분류 테스트 탭의 입력·모델 선택·평가·출력 계약은 유지된다. 탭을 오가도 입력/결과와 대화는 별도 상태로 남는다. 챗봇의 연결은 기존 vLLM Qwen으로 고정하며 UI의 분류 테스트 모델 선택을 따라가지 않는다.

처리: `POST /api/chat/sessions → POST /api/chat/sessions/{session_id}/messages → 같은 Qwen의 분석+매핑+reply JSON 1회 생성 → 서버 검증 → 대화 표시`.
세션 생성/읽기는 생성 API를 호출하지 않는다. `GET /api/chat/sessions/{session_id}`는 기록만 읽는다. 새 대화 버튼은 별도 세션을 만들며 과거 기록을 삭제하지 않는다.

- 프롬프트: `prompts/chatbot_v2.md`, 버전 `chat-intent-explanation-v1.0.2`
- 스키마/한도: `backend/chat_schema.py`, 출력 4096토큰. 기존 분류 테스트는 256토큰/배열 계약 그대로다.
- 부분 모호·부분 범위 밖 정책: `chat-partial-request-policy-draft-v1.0.0`. 명확한 부분은 유지하고 각 부분을 따로 표시한다. 기존 평가 정답은 변경하지 않는다.
- 맥락: 서버가 같은 세션의 최근 최대8개 교환·12,000자를 통째로 선택한다. 더 오래된 교환은 제외하고 제외된 turn_id/수/한도를 기록한다. 현재 질문은 별도 포함한다. 이전 AI 원문은 저장·화면에 남기지만 모델 맥락에는 `unverified_interpretation`의 분류 ID만 전달한다. 사용자 원문은 실제 턴 ID와 함께 보내고 업무 수행·조회 결과·구체 제안은 없다는 메타데이터를 붙인다. 내부 thinking/오류 출력은 넣지 않는다. 이전 사용자 원문을 실제 출처로 사용하며 최신 정정을 우선하도록 지시한다. 맥락 제한 때문에 누락된 내용은 복원하지 않는다.
- 출처 검사(v2.0.0): 대상·기간·조건은 정규화한 해석이며 원문 부분 문자열일 필요가 없다. 요청별 `evidence=[{turn_id,quote}]`가 실제 같은 세션의 전달된 사용자 턴에 연결되고, quote가 공백·조사·숫자까지 동일한 연속 구절인지 검사한다. 다른 세션·모델 발언·없는 턴/인용·인용 누락은 오류다. 인용 실재와 의미 지지는 별개이며 의미는 자동 검증하지 않는다. 과거 `context_used` 기록은 이전 계약으로 그대로 읽는다.
- 저장: `data/chatbot/sessions.sqlite3`에 세션·원문·분석·응답·호출/시간/토큰/버전을, `data/chatbot/requests.jsonl`에 완료/오류 감사 기록을 따로 저장한다. 기존 분류 평가/작업 기억과 섞지 않는다. 인증 헤더를 기록하지 않는다.
- 처리: 같은 대화의 중복 진행 요청은409로 거절하고, 기존 분류 테스트와 같은 프로세스 전역 모델 lock을 사용한다. 기존 `evaluation.lock`이 활성화된 평가에서는 신규 추론을 차단한다. 이번 챗봇 재평가는 자체 요청만 순차 실행하고 다른 API 이용을 중지하지 않았으므로 공유 서버 조건이다. 현재 실행 방식은 단일 Uvicorn worker다. 재시작 시 미완료 기록을 interrupted 오류로 보존하며 자동 재시도하지 않는다.
- 오류: 스키마·ID·추가 필드·빈 요청·출처·잘림·API/타임아웃을 오류로 기록한다. 출력 보정·ID 수정·재호출·다른 모델 대체 없음. 오류 뒤 다른 질문을 보낼 수 있다. 요청이 모델 가용성 검사에서 차단되면 생성 호출은0회이며, 정상 접수한 모델 요청은1회다.
- 보안/운영 범위: 로컬 프로토타입이며 세션 ID는 추측하기 어려운 UUID다. 사용자 인증/다중 사용자 권한 관리는 이번 범위에 없다. 원격 공유가 필요하면 이 ID를 공개하지 않는다.

챗봇은 원인·해결책·조건 추천·실제 작업 계획을 생성하지 않고, 적용 요청도 **적용 의도**로만 분류한다. DB/검색/예측/승인/설비/트윈 실행을 연결하지 않는다. 1차66개 정의를 프롬프트에 넣지 않는다.

검증:

```bash
cd /home/yt/lam
.venv/bin/python -m pytest -q
# 실제 모델 확인: 새 실험ID 사용, 기존 질문 실패의 자동 재시도 없음
.venv/bin/python -m evaluation.check_chatbot --experiment chatbot-<새ID>
```

실측 기록과 보고서는 `data/chatbot/validation/<ID>/`에 분리 저장한다. 정의/프롬프트/기대 ID는 실행 전에 고정하며 정답은 모델에 전달하지 않는다. 실제 모델 샘플 확인은 전체 의도 분류 성능의 통계적 평가가 아니다.

복원: 서비스를 멈춘 뒤 `data/archive/before-chatbot-20261029/`의 원래 상대 경로 파일을 복사하고 서비스를 다시 시작하면 기존 분류 테스트 전용 화면/전송 경로로 되돌아간다. 챗봇 데이터/과거 평가/모델 파일을 삭제할 필요는 없다.


### 챗봇 한국어 표현 정리 (2026-10-29)

챗봇 전용 정의는 `taxonomy/intents_v2_ko.json`이며 버전은 `food-manufacturing-second-review-draft-ko-v2.0.1`이다. 원본 `intents_v2.json`의 혼합 한자 표기·일본어 문장부호만 한국어/일반 문장부호로 정리했다. 15개 ID·명칭·분류 의미는 유지하며, 기존 분류 테스트·과거 평가의 원본 정의는 바꾸지 않았다. 구체적인 전후 내역은 `data/archive/before-korean-chatbot-20261029/definition_changes.json`에 있다.

과거에 적용한 챗봇 프롬프트 v1.0.2는 사용자 설명·분류 이유·확인 질문을 자연스러운 한국어로 작성하고, 제품명·의도 ID·태그·기술 약어는 원문대로 유지하도록 지시한다. 이전 대화의 혼합 표현도 새 설명에서 따라 쓰지 않게 한다. 과거 응답/대화는 수정하지 않으며, 생성된 출력의 문자열 치환·사후 보정·언어 필터 재시도를 하지 않는다.

원본 요청/응답·버전·질문별 의도·문자 검사·기존 기록 보존 확인은 `data/chatbot/validation/chatbot-korean-20261029-01/`에 별도 저장한다. 실행 명령은 기존 `evaluation.check_chatbot --experiment <새ID>`다. `evaluation.check_chatbot_language`는 이번에 고정한 추가 언어 회귀 확인용이며, 같은 결과를 덮어쓰거나 자동 재시도하지 않는다. 제품명의 원문 한자는 허용하며 중국어/일본어 설명 혼입과 구분한다. 소수 사례에서 혼입이 없더라도 모든 향후 응답을 보장하는 것은 아니다.


### 원문 근거·해석 계약 v2.0.0 (2026-10-30)

현재 챗봇 프롬프트는 `chat-intent-explanation-v2.0.0`, 스키마는 `chat-intent-schema-v2.0.0`, 검증 계약은 `user-quote-interpretation-v2.0.0`이다. `prompts/chatbot_v2.md`, `backend/chat_schema.py`, `backend/chat_contract.py`에서 관리한다. 기존 분류 테스트의 15개 정의·배열 계약과 Qwen 설정은 유지했다.

원문 근거는 실제 사용자 턴의 정확한 연속 인용이고, 해석은 의미를 정리한 문장이다. 문자열 검사는 인용에만 엄격히 적용한다. 이전 조회 요청을 조회 결과로, 추천 요청을 존재하는 제안으로 취급하지 않도록 프롬프트와 맥락을 구분했다. 실제 기능이나 두 번째 LLM 검증은 추가하지 않았다. 형식·출처 오류는 API 오류와 구분해 저장하고, 의미 오류는 자동으로 검증됐다고 표시하지 않는다.

기존414건과 새로운72건을 각 묶음3회 반복한 실제 챗봇 재평가, 원본·정답·해시·부정 테스트·UI 캡처·복원 명령은 `evaluation/results/chatbot-contract-20261029-01/`에 있다. ID 일치와 별개로 설명·수량 정정·출처 포괄성 오류가 남았으며 UI 별도4개 표본에도 오분류1개를 그대로 보존했다. 보고서는 `report.md`, 복원은 `restore.md`다. 과거 점수와 원본은 변경하지 않았다.

```bash
.venv/bin/pytest -q
# 이미 완료한 생성은 다시 호출하지 않음. 중단 기록 복구/미실행 항목만 재개
.venv/bin/python evaluation/contract/resume_ordered.py --resume
# 저장 자료만 재집계: 모델 호출 없음
.venv/bin/python evaluation/contract/score.py
.venv/bin/python evaluation/contract/report.py
```

위의 예전 `check_chatbot_language`는 과거 스키마용 회귀 도구로 버전 보호하며 새 계약에 그대로 사용하지 않는다. 분류 전용 평가와 이번 챗봇 평가도 구분한다. 새 계약의 잘못된 숫자·기간·허위 수행 표현은 정확한 인용이 붙어도 기계적으로 통과할 수 있으므로, 부정 테스트와 AI 의미 검토 결과를 별도로 읽어야 한다.


## 현재 챗봇 v3: 분석 검증 후 실제 스트리밍 (2026-11-02)

현재 웹 챗봇은 위 v2의 단일 reply JSON 경로 대신 **메시지 저장 수락 → 구조화 분석·출처 검증 → 서버 gate → 실제 vLLM 응답 스트리밍**을 사용한다. 정상/clarify는 최대2회, 범위 밖만 있거나 분석 오류이면 응답 생성0회다. 분석에는 reply를 생성하지 않는다. 기존 분류 테스트의 계약·정의·프롬프트는 그대로다.

Enter 전송·Shift+Enter 줄바꿈·한글 조합 Enter 보호, 수락 시 전송 초안만 초기화, 요청ID 중복 방지, 세션 목록·새로고침 복원·중단·부분 저장·JSONL 내보내기를 추가했다. 같은 슬롯의 정정은 대체하고 철회는 제거하며 별도 업무는 이전 활성 맥락을 상속하지 않는다. 모델이 정리한 기억은 업무 수행 사실이나 골든 라벨이 아니다.

`settings/mock_tools.json`의17개 항목은 **모의 도구 구성 · 실제 실행 안 함**이다. 쓰기 비활성·승인 조건을 유지하며 실제/native tool calling이나 업무 DB/검색/예측/디지털 트윈을 연결하지 않는다.

처리/저장/API/버전/제한은 [docs/chat_stream.md](docs/chat_stream.md), 실제 검증 원본과 보고서는 `evaluation/results/chatbot-stream-20261102-01/`에 있다. 과거 v2 평가의 질문·점수·출력·가중치는 보존했다. 과거 평가용 c.chat/스크립트를 현재 웹 경로의2단계 성능으로 오해하지 않는다. 이번 원본도 실패/중단을 포함해 보존하며 자동 재생성하지 않는다.
