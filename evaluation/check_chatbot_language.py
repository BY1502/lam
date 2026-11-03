"""Language regression checks: raw outputs are saved, never edited or retried."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend import chatbot,chat_schema as spec,config,llm_client,classifier
from evaluation.check_chatbot import api,dump,sha
OUT=ROOT/'data/chatbot/validation/chatbot-korean-20261029-01'
CASES=[
 {'id':'documentation','question':'재가동 기준이 매뉴얼의 어느 부분에 기재되어 있는지 알려줘.','expected_ids':['knowledge.lookup'],'allowed_original_names':[]},
 {'id':'connection','question':'검사 기록과 센서 기록이 같은 LOT로 연결되어 있는지 평가해줘.','expected_ids':['data.analyze'],'allowed_original_names':[]},
 {'id':'process','question':'고장 원인과 후속 공정에 미치는 영향을 분석해줘.','expected_ids':['cause.analyze'],'allowed_original_names':[]},
 {'id':'technical_names','question':'제품 Matcha-A의 T0200 태그와 PLC 매뉴얼의 관계를 찾아줘.','expected_ids':['knowledge.lookup'],'allowed_original_names':['Matcha-A','T0200','PLC']},
 {'id':'original_product_name','question':'제품 抹茶-A의 SOP 설정 기준을 알려줘.','expected_ids':['knowledge.lookup'],'allowed_original_names':['抹茶-A','SOP']},
 {'id':'reported_question','question':'라면 물 온도는 몇도가 적당해?','expected_ids':['knowledge.lookup'],'allowed_original_names':[],'expectation_note':'기존 혼합 표현이 관찰된 질문의 저장 ID와 회귀 비교. 해당 ID 자체를 새 의미적 정답으로 확정하지 않음.'}]

def main():
 if getattr(spec,'CONTRACT_VERSION',None):raise RuntimeError('이 확인기는 과거 스키마의 보존용입니다. 새 계약은 evaluation/contract의 실제 챗봇 검증을 사용하세요.')
 mp=OUT/'additional_language_manifest.json'
 if mp.exists():raise RuntimeError('기록을 덮어쓰거나 생성 요청을 재시도하지 않습니다.')
 originals=[json.loads(x) for x in (ROOT/'data/chatbot/requests.jsonl').read_text().splitlines()]
 original=next(r for r in originals if r['request_id']=='eb802e4afd80485d92317377fd3752ee')
 fixture={'id':'prior_mixed_history','question':'같은 제품의 작업표준서 설정 기준을 알려줘.','expected_ids':['knowledge.lookup'],'source_request_id':original['request_id'],'source_role':'복사한 이전 원문을 독립 테스트 입력으로만 사용. 기존 세션/응답은 수정하지 않음.','allowed_original_names':[]}
 dump(mp,{'versions':chatbot.version_info(),'cases':CASES,'history_fixture':fixture,'hashes':{p:sha(ROOT/p) for p in ['prompts/chatbot_v2.md','taxonomy/intents_v2_ko.json','backend/chatbot.py','backend/chat_schema.py','evaluation/check_chatbot_language.py']}})
 rows=[]
 for case in CASES:
  sid=api('/api/chat/sessions',{})['session_id'];r=api('/api/chat/sessions/'+sid+'/messages',{'question':case['question']});row={'case':case,'record':r};rows.append(row)
  with (OUT/'additional_language_runs.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush()
  print(case['id'],r['status'],[x['intent_id'] for x in (r.get('analysis') or {}).get('requests',[])],r.get('error'),flush=True)
 # A separate test request with an untouched copy of the reported historical response.
 target=config.targets()['vllm_qwen'];messages,limits,sources=chatbot.build_messages(fixture['question'],[original])
 with classifier.LOCK:
  if not llm_client.probe(target)['can_run']:raise RuntimeError('vLLM unavailable')
  r=llm_client.infer(target,messages,schema=spec.SCHEMA,max_tokens=spec.MAX_TOKENS)
 r['analysis']=chatbot.validate(r['content'],r['finish_reason'],r['tool_calls'],fixture['question'],sources);r['context_limits']=limits;r['llm_call_count']=1
 dump(OUT/'historical_context_fixture.json',{'case':fixture,'record':r,'copied_original_record':original});print('historical fixture',r['analysis']['reply'],flush=True)
 frozen=json.loads(mp.read_text());assert all(sha(ROOT/f)==h for f,h in frozen['hashes'].items())
if __name__=='__main__':main()
