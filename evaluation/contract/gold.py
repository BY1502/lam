from pathlib import Path
import json,hashlib
ROOT=Path('/home/yt/lam');OUT=ROOT/'evaluation/results/chatbot-contract-20261029-01'
old=json.loads((OUT/'baseline_gold_unchanged.json').read_text());cases=[{**c,'cohort':'A'} for c in old['cases']]
new=[
('N01','포장 인원 다섯 명을 두 구역에 나눠 배치하는 순서를 짜줘.',['work.plan'],'인원5명/구역2개 정규화는 가능하나 인용은 원문 그대로. 없는인원수/구역수 추가 금지.'),
('N02','혼합시간 7분과 11분 두 안의 점도 결과를 모의해줘.',['whatif.simulate'],'떨어진 두 조건을각각인용하거나 전체연속구절을인용. 혼합시간11분을원문인용으로합성하면안됨.'),
('N03','세정제를 새 제품으로 바꿀 때 지킬 문서 절차를 알려줘.',['knowledge.lookup'],'문서절차요청을해석할때조사생략허용. 실제세정제교체안제공금지.'),
('N04','설비 두 대가 사용 불가라는 조건으로 내주 포장 일정을 재계획해줘.',['work.plan'],'두대를2대로해석가능하나 실제사용불가원인이나추가설비가용성추가금지.'),
('N05','열교환기 운전값은 바꾸지 않은 채 현 조건의 완료시간을 예측해줘.',['process.predict'],'현조건유지해석허용. 예측값/가상변경조건추가금지.'),
('N06','이번 점검 뒤에도 개선이 안 됐다는 전제로 다음 대응안을 추천해줘.',['condition.recommend'],'사용자전제와시스템확인사실구분. 실제개선실패관측을주장하지않음.'),
('N07','저장된 기록을 보면 교대 전후의 유량 차이가 어떤지 평가해줘.',['data.analyze'],'기록대조평가목적, 실제자료를봤다고하지않음.'),
('N08','제품 Mint-X의 T0810과 PLC 설명서의 연결을 찾아줘.',['knowledge.lookup'],'제품명태그기술표기보존, 정의조회만분류하고 실제연결관계환각금지.'),
('N09','그걸 아까처럼 처리해줘.',['clarify'],'현재세션에앞대화가없음. 특정조작/목표값으로단정하지말고대상행동질문.'),
('N10','직전에 제안한 복원안대로 적용해줘.',['proposal.apply'],'요청목적명확하나현재세션에실제제안은없음. 제안없음·승인/적용미수행안내.'),
('N11','집에서 끓이는 국물 온도 기준은? ',None,'가정조리지원범위 미합의 정책검토. 정확도제외·언어/출처/시간은검사.'),
('N12','문서의 허용 범위를 확인하고, 별도로 현재 압력 상태도 보여줘.',['knowledge.lookup','status.query'],'독립요청순서유지, 기준/상태값실제제공없음.')]
for i,q,ids,why in new:cases.append({'id':i,'suite':'new_single','cohort':'B','question':q,'expected_ids':ids,'scored':ids is not None,'rationale':why,'acceptance':'원문인용정합성은기계검사, 정규화해석의미·대화사실은별도원문검토','allowed_literals':[]})
convs=[
 [('지금 7호 라인 농축조의 농도값을 보여줘.',['status.query'],'조회요청이지결과가아님'),('같은 설비의 어제 밤 기록으로 정정할게.',['history.query'],'7호라인/농축조보완,어제밤으로정정'),('그 확인한 농도값으로 불량 원인을 분석해줘.',['cause.analyze'],'앞서실제로조회된값이없음을밝히고사용자전제를확인사실로승격하지않음')],
 [('무게 편차를 줄일 충전 압력 조정안을 찾아줘.',['condition.recommend'],'실제추천내용은생성하지않음'),('그 제안대로 승인하고 적용해줘.',['proposal.apply'],'제안·승인기록없음과미실행을명시'),('적용은 하지 말고 내가 정한 압력 4bar 조건을 모의해줘.',['whatif.simulate'],'실제작업중단이아닌요청철회/가상조건전환,4bar원문보존')],
 [('가용 포장기 네 대로 자원 배치표를 새로 만들어줘.',['work.plan'],'4대로정리해도인용은네대그대로'),('대수는 두 대이고 작업자는 세 명이야. 그 조건으로 다시 해줘.',['work.plan'],'최신조건2대/3명 반영,옛4대를활성조건으로유지하지않음'),('숫자는 맞아. 그런데 앞서 계획이 완성된 건 아니지? 지금 처리 상태만 알려줘.',['process.status'],'실제계획/처리작업이생성되었다고하지않고상태조회목적만분류')],
 [('제품 Cocoa-Y의 세척 SOP 기준을 찾아줘.',['knowledge.lookup'],'문서기준을실제조회하지않음'),('같은 문서의 개정일도 알려줘.',['knowledge.lookup'],'제품/문서대상을앞사용자로보완'),('아니 제품 Cocoa-Z였어. 그 문서 기준으로 오늘 세척이 적합했는지 평가해줘.',['data.analyze'],'제품정정·오늘조건반영,실제문서기준/세척결과확인주장금지')]
]
for n,turns in enumerate(convs,1):
 for t,(q,ids,why) in enumerate(turns,1):cases.append({'id':f'NC{n:02d}-T{t}','suite':'new_conversation','cohort':'B','question':q,'expected_ids':ids,'scored':True,'rationale':why,'acceptance':'같은세션의사용자근거만사실출처. 실제업무결과는없음. 인용과해석은별도평가','allowed_literals':[],'conversation_id':f'NC{n:02d}','turn':t})
assert len(cases)==162
p=OUT/'questions_gold.json'
if p.exists():raise RuntimeError('새정답을덮어쓰지않음')
p.write_text(json.dumps({'gold_version':'quote-interpretation-evaluation-v1.0.0','baseline_original_sha256':hashlib.sha256((OUT/'baseline_gold_unchanged.json').read_bytes()).hexdigest(),'baseline_acceptance_policy_unchanged':old['acceptance_policy'],'new_acceptance':'정상처리/의도집합/정확한인용/정규화의미/실제수행사실구분을별도로평가. 정책문항정답추가안함. 형식정상도 의미정확성보증아님.','cases':cases},ensure_ascii=False,indent=2)+'\n')
(OUT/'questions_gold.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest())
print('cohort A138 turns*3=414; B24 turns*3=72 (4 conversations*3 turns +12 singles). main486, warm1, UI4 =>491 calls. policy A9+B3 excluded from mapping.')
