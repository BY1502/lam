"""Read complete first occurrences, then exact text diffs of repeat outputs already read.
No judgments generated; all changes are presented and final judgments explicitly committed by AI.
"""
import json,sys,difflib
from evaluation.contract.review import export,rows,OUT

def flatten(x,path=''):
 if isinstance(x,dict):return {k:v for key,value in x.items() for k,v in flatten(value,path+'.'+key).items()}
 if isinstance(x,list):return {k:v for i,value in enumerate(x) for k,v in flatten(value,path+f'[{i}]').items()}
 return {path:x}
def main():
 gs=export();reviews={r['group_id']:r for r in rows(OUT/'manual_reviews.jsonl')};pending=[g for g in gs if g['id'] not in reviews]
 for g in pending[:int(sys.argv[1] if len(sys.argv)>1 else 12)]:
  bases=[b for b in gs if b['id'] in reviews and b['case_id']==g['case_id'] and b['history_users']==g['history_users']]
  view={'id':g['id'],'case':g['case_id'],'error':g['error']}
  if not bases:view.update(full={k:g[k] for k in ['question','expected','history_users','generation']})
  else:
   current=flatten(g['generation']);base=min(bases,key=lambda b:sum(current.get(k)!=v for k,v in flatten(b['generation']).items()));old=flatten(base['generation']);diff=[]
   for k in sorted(set(old)|set(current)):
    if old.get(k)==current.get(k):continue
    a,b=old.get(k),current.get(k)
    diff.append([k,b])
   view.update(base=base['id'],question=g['question'],expected=g['expected'],base_judgment={k:reviews[base['id']][k] for k in ['explanation_judgment','normalization_judgment','evidence_support_judgment','context_judgment','performed_fact_error','reason']},exact_changed_fields=diff)
  print(json.dumps(view,ensure_ascii=False))
if __name__=='__main__':main()
