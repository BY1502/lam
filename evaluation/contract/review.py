"""Derived review exports only. Does not alter runs or application output."""
import hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'evaluation/results/chatbot-contract-20261029-01'
def rows(p):
 data=[json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
 if p.name=='runs.jsonl' and (OUT/'harness_incident.json').exists():
  excluded=set(json.loads((OUT/'harness_incident.json').read_text())['excluded_request_ids'])
  data=[r for r in data if r['record'].get('request_id') not in excluded]
 return data
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def generated(r):
 a=r.get('analysis')
 if a:return a
 try:
  a=json.loads(r.get('content') or '')
  return a if isinstance(a,dict) else {'invalid_raw':r.get('content')}
 except Exception:return {'invalid_raw':r.get('content') or '', 'displayed_error':r.get('reply')}
def export():
 data=rows(OUT/'runs.jsonl');gold={r['id']:r for r in json.loads((OUT/'questions_gold.json').read_text())['cases']};existing=json.loads((OUT/'review_groups.json').read_text()) if (OUT/'review_groups.json').exists() else [];byhash={g['hash']:g for g in existing}
 session_text={};used_hashes=set()
 for r in data:
  rr=r['record']
  if rr.get('request_id'):session_text[rr['request_id']]=gold[r['case_id']]['question']
 for row in data:
  c=gold[row['case_id']];r=row['record'];a=generated(r);normal=json.loads(json.dumps(a))
  # Equality grouping only; original response text stays in raw runs. ID references resolve to actual user text.
  for part in normal.get('requests',[]) if isinstance(normal.get('requests',[]),list) else []:
   if isinstance(part,dict):
    for src in part.get('evidence',[]) if isinstance(part.get('evidence',[]),list) else []:
     if isinstance(src,dict) and 'turn_id' in src:src['turn_id']=session_text.get(src['turn_id'],src['turn_id'])
  history=[]
  for msg in r.get('request',{}).get('messages',[])[1:-1]:
   if msg['role']=='user':
    try:history.append(json.loads(msg['content'])['text'])
    except Exception:history.append(msg['content'])
  h=hashlib.sha256(json.dumps({'case_id':row['case_id'],'generation':normal,'history_users':history},ensure_ascii=False,sort_keys=True).encode()).hexdigest();key=f"{row['repeat']}:{row['case_id']}";used_hashes.add(h)
  if h not in byhash:
   g={'id':f'G{len(existing)+1:04d}','hash':h,'case_id':row['case_id'],'suite':c['suite'],'cohort':c['cohort'],'question':c['question'],'expected':c['expected_ids'],'rationale':c['rationale'],'criteria':c.get('context_criteria',{}),'history_users':history,'generation':normal,'status':r['status'],'error':r.get('error'),'members':[]};existing.append(g);byhash[h]=g
  if key not in byhash[h]['members']:byhash[h]['members'].append(key)
 for g in existing:g['excluded_from_primary']=g['hash'] not in used_hashes
 dump(OUT/'review_groups.json',existing);return [g for g in existing if not g['excluded_from_primary']]
if __name__=='__main__':
 groups=export();reviews=rows(OUT/'manual_reviews.jsonl');reviewed={r['group_id'] for r in reviews};pending=[g for g in groups if g['id'] not in reviewed]
 if len(sys.argv)>1:
  n=int(sys.argv[1]);chosen=pending[:n]
  for g in chosen:
   view={k:g[k] for k in ['id','case_id','question','expected','criteria','history_users','generation','error']};print(json.dumps(view,ensure_ascii=False))
 else:print(json.dumps({'runs':len(rows(OUT/'runs.jsonl')),'groups':len(groups),'reviewed':len(reviewed),'pending':len(pending)}))
