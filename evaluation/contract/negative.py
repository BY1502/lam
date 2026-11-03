"""Negative fixtures: distinguish mechanical rejection from semantic-review failures."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend.chat_contract import validate,ContractError
OUT=ROOT/'evaluation/results/chatbot-contract-20261029-01'
q='포장 인원 여섯 명을 세 작업에 배치해줘.'
source={'t-user':{'text':q,'session_id':'s','role':'user'},'t-old':{'text':'압력 기록을 보여줘.','session_id':'s','role':'user'},'other-session':{'text':q,'session_id':'other','role':'user'},'model-turn':{'text':q,'session_id':'s','role':'assistant'}}
def base():return {'requests':[{'interpreted_request':'인력 배치 계획','intent_id':'work.plan','reason':'자원배치계획 요청','target_expressions':['포장 인원'],'time_expression':None,'conditions':['인원 6명','작업 3개'],'evidence':[{'turn_id':'t-user','quote':q}]}],'reply':'계획 수립 요청을 이해했습니다. 실제 계획은 작성하지 않았습니다.'}
cases=[]
def add(name,fn,mechanical,semantic,why):
 a=base();fn(a);cases.append({'id':name,'output':a,'expected_mechanical_accept':mechanical,'expected_semantic_judgment':semantic,'reason':why})
add('valid_normalization',lambda a:None,True,'통과','숫자/조사 정규화는해석이며정확한별도인용이존재')
add('nonexistent_quote',lambda a:a['requests'][0]['evidence'][0].update(quote='원문에 없는 내용'),False,'실패','연속인용실재검사')
add('wrong_turn',lambda a:a['requests'][0]['evidence'][0].update(turn_id='t-old'),False,'실패','다른실제턴의텍스트에는해당인용이없음')
add('wrong_session',lambda a:a['requests'][0]['evidence'][0].update(turn_id='other-session'),False,'실패','현재세션소속검사')
add('model_as_user',lambda a:a['requests'][0]['evidence'][0].update(turn_id='model-turn'),False,'실패','assistant를사용자근거로인용금지')
add('invented_number_in_quote',lambda a:a['requests'][0]['evidence'][0].update(quote='포장 인원 999명'),False,'실패','인용은정규화되지않으며없는숫자는거절')
add('missing_link',lambda a:a['requests'][0].update(evidence=[]),False,'실패','요청단위의원문근거연결필수')
add('invented_number_in_interpretation',lambda a:a['requests'][0].update(conditions=['인원 999명']),True,'실패','문자열검증은의미왜곡을증명하지못함. 기계통과를의미정확성으로집계하면안됨')
add('invented_time_in_interpretation',lambda a:a['requests'][0].update(time_expression='2099년 3월 1일'),True,'실패','출처는실재하지만해석의시각추가를의미검토에서거절')
add('requested_query_as_result',lambda a:a.update(reply='이전 요청에서 압력값을 조회해 확인했습니다.'),True,'실패','인용실재와별개로실제조회결과없음')
add('recommend_request_as_actual_proposal',lambda a:a.update(reply='앞서 제시한 구체적인 장력 조정안이 있으므로 승인하고 적용했습니다.'),True,'실패','추천요청만있어도실제제안·승인·실행으로바꾸면의미오류')
a=base();a['requests'][0]['evidence']=[{'turn_id':'t-old','quote':'압력 기록'}];cases.append({'id':'valid_but_irrelevant_quote','output':a,'expected_mechanical_accept':True,'expected_semantic_judgment':'실패','reason':'존재하는같은세션구절이라도인력배치해석을뒷받침하지않으므로의미오류'})
split={'split':{'text':'혼합시간 8분과 12분의 조건을 비교해줘.','session_id':'s','role':'user'}}
a=base();a['requests'][0]['evidence']=[{'turn_id':'split','quote':'혼합시간 12분'}];cases.append({'id':'joined_noncontiguous_quote','output':a,'sources':split,'expected_mechanical_accept':False,'expected_semantic_judgment':'실패','reason':'떨어진어구를연속인용으로만들지않음'})
p=OUT/'negative_gold.json'
if p.exists():raise RuntimeError('부정기준을덮어쓰지않음')
p.write_text(json.dumps({'cases':cases,'default_sources':source,'note':'합성단위부정테스트이며LLM을호출하지않음. 의미판정정답을기계검증기가자동으로산출했다고주장하지않음.'},ensure_ascii=False,indent=2))
results=[]
for case in cases:
 try:returned=validate(json.dumps(case['output'],ensure_ascii=False),'stop',[],case.get('sources',source),session_id='s');accepted=True;kind=None;assert returned==case['output']
 except ContractError as exc:accepted=False;kind=exc.kind
 results.append({'id':case['id'],'mechanical_accepted':accepted,'expected_mechanical':case['expected_mechanical_accept'],'mechanical_correct':accepted==case['expected_mechanical_accept'],'error_kind':kind,'semantic_expected':case['expected_semantic_judgment'],'semantic_verified_by_code':False,'reason':case['reason']})
assert all(r['mechanical_correct'] for r in results)
(OUT/'negative_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print('negative fixture cases',len(results),'mechanical expected outcomes matched; semantic checks explicitly separate')
