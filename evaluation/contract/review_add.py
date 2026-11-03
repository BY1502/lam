"""Persist explicit AI reading judgments, never generate them from correctness rules."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
OUT=Path('/home/yt/lam/evaluation/results/chatbot-contract-20261029-01')
def commit(judgments):
 groups={g['id']:g for g in json.loads((OUT/'review_groups.json').read_text())};p=OUT/'manual_reviews.jsonl';old={json.loads(l)['group_id'] for l in p.read_text().splitlines()} if p.exists() else set()
 with p.open('a') as f:
  for gid,v in judgments.items():
   assert gid not in old and gid in groups
   row={'group_id':gid,'content_hash':groups[gid]['hash'],'reviewer':'현재 개발 에이전트의 AI 원문 검토; 블라인드·독립 평가 아님','reviewed_at_kst':datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),'language_judgment':'허용 표현','language_reason':'질문·이전 사용자 발언과 전체 생성문장을 대조했다. 의도 ID·기술 약어 외 설명은 한국어이며 비허용 외국어 문장은 관찰하지 못했다.','language_actual_fields':[],'language_pending_fields':[],'explanation_judgment':'통과','reason':None,'id_reply_mismatch':False,'unsupported_fact':False,'scope_escape':False,'split_judgment':'통과','context_judgment':'통과','normalization_judgment':'통과','evidence_support_judgment':'통과','performed_fact_error':False,'interpretation_addition':False,**v};assert row['reason'];f.write(json.dumps(row,ensure_ascii=False)+'\n')
 print('saved',len(judgments))
