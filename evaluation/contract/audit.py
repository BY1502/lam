"""Read-only provenance checks and paired regression tables. Never generates model text."""
import csv,hashlib,json,sqlite3,subprocess,sys
from pathlib import Path
from collections import defaultdict,Counter
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend import chatbot
from evaluation.contract.review import rows,export,generated
from evaluation.contract.score import ids,correct,good
from evaluation.run_classification import inspect,fingerprint
OUT=ROOT/'evaluation/results/chatbot-contract-20261029-01'
OLD=ROOT/'evaluation/results/chatbot-eval-20261029-01'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def table(name,rs):
 if not rs:return
 with (OUT/name).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
def main():
 m=json.loads((OUT/'manifest.json').read_text());data=rows(OUT/'runs.jsonl');raw=[json.loads(l) for l in (OUT/'runs.jsonl').read_text().splitlines()];gold={r['id']:r for r in json.loads((OUT/'questions_gold.json').read_text())['cases']};groups=export();reviews={r['group_id']:r for r in rows(OUT/'manual_reviews.jsonl')};member={key:g for g in groups for key in g['members']}
 assert (OUT/'baseline_gold_unchanged.json').read_bytes()==(OLD/'questions_gold.json').read_bytes()
 for oldcase in json.loads((OLD/'questions_gold.json').read_text())['cases']:
  assert all(gold[oldcase['id']].get(k)==v for k,v in oldcase.items())
 assert len(data)==486 and len({(r['repeat'],r['case_id']) for r in data})==486
 assert all(len([r for r in data if r['case_id']==iid])==3 for iid in gold)
 assert all(g['id'] in reviews for g in groups)
 assert all(sha(ROOT/f)==h for f,h in m['hashes'].items())
 preserve=json.loads((OUT/'preservation_before.json').read_text());changed=[f for f,h in preserve['files'].items() if sha(ROOT/f)!=h]
 with sqlite3.connect(ROOT/'data/chatbot/sessions.sqlite3') as c:
  nowhash={r[0]:hashlib.sha256(json.dumps(list(r),ensure_ascii=False).encode()).hexdigest() for r in c.execute('SELECT request_id,session_id,created_at,question,status,record_json FROM turns')}
 altered=[k for k,h in preserve['turn_hashes'].items() if nowhash.get(k)!=h];prefix=(ROOT/'data/chatbot/requests.jsonl').read_bytes()[:preserve['log_prefix_bytes']];prefix_ok=hashlib.sha256(prefix).hexdigest()==preserve['log_prefix_sha256']
 assert not changed and not altered and prefix_ok
 dump(OUT/'preservation_after.json',{'checked_at':datetime.now(timezone.utc).isoformat(),'protected_files':len(preserve['files']),'changed_files':changed,'old_turns':len(preserve['turn_hashes']),'changed_or_missing_old_turns':altered,'original_log_prefix_unchanged':prefix_ok})
 previous=defaultdict(list);reconstruction=[];source_audit=[]
 for row in data:
  r=row['record'];sid=row['session_id'];expected,limits,sources=chatbot.build_messages(gold[row['case_id']]['question'],previous[sid],current_turn_id=r['request_id'],session_id=sid)
  reconstruction.append(expected==r.get('request',{}).get('messages'))
  a=generated(r);checks=[]
  for req in a.get('requests',[]):
   ev=req.get('evidence',[]);checks.append(bool(ev))
   for e in ev:
    src=sources.get(e.get('turn_id'));checks.append(bool(src and src['role']=='user' and src['session_id']==sid and e.get('quote') and e['quote'] in src['text']))
  source_audit.append({'repeat':row['repeat'],'case_id':row['case_id'],'request_id':r['request_id'],'mechanical_source_valid':bool(checks) and all(checks),'meaning_automatically_verified':False})
  previous[sid].append(r)
 assert all(reconstruction)
 table('source_audit.csv',source_audit)
 base=(ROOT/'prompts/chatbot_v2.md').read_text()+'\n'+json.dumps(chatbot.load_taxonomy(),ensure_ascii=False)
 assert not any(c['question'] in base for c in gold.values())
 oldrows=rows(OLD/'runs.jsonl');old={(r['repeat'],r['case_id']):r for r in oldrows};paired=[];former=[]
 for r in data:
  if r['cohort']!='A':continue
  o=old[r['repeat'],r['case_id']];rv=reviews[member[f"{r['repeat']}:{r['case_id']}"]['id']];c=gold[r['case_id']]
  rr={'repeat':r['repeat'],'case_id':r['case_id'],'question':c['question'],'scored':c['scored'],'old_status':o['record']['status'],'new_status':r['record']['status'],'old_error':o['record'].get('error_kind'),'new_error':r['record'].get('error_kind'),'old_ids':json.dumps(ids(o)),'new_ids':json.dumps(ids(r)),'old_correct':correct(o,gold) if c['scored'] else None,'new_correct':correct(r,gold) if c['scored'] else None,'old_backend_seconds':o['record'].get('elapsed_seconds'),'new_backend_seconds':r['record'].get('elapsed_seconds'),'new_explanation':rv['explanation_judgment'],'new_normalization':rv['normalization_judgment'],'new_evidence_support':rv['evidence_support_judgment'],'new_performed_fact_error':rv['performed_fact_error'],'new_review_reason':rv['reason'],'new_request_id':r['record']['request_id']}
  paired.append(rr)
  if o['record'].get('error_kind'):former.append(rr)
 table('paired_baseline.csv',paired);table('former_29_failures.csv',former)
 assert len(former)==29
 repeats=[]
 for iid,c in gold.items():
  rs=[r for r in data if r['case_id']==iid];cnt=sum(correct(r,gold) for r in rs) if c['scored'] else None
  repeats.append({'case_id':iid,'cohort':c['cohort'],'repeats':len(rs),'correct_runs':cnt,'ids_status_consistent':len({(r['record']['status'],tuple(ids(r))) for r in rs})==1,'normalized_source_support':[reviews[member[f"{r['repeat']}:{r['case_id']}"]['id']]['evidence_support_judgment'] for r in rs]})
 table('repeat_stability.csv',repeats)
 ui=rows(OUT/'ui_runs.jsonl');warm=rows(OUT/'warmups.jsonl');assert len(ui)==4 and len(warm)==1
 accepted=[r['record'] for r in [*raw,*ui,*warm]]
 assert all(r['llm_call_count']==1 and 'tools' not in r['request'] and not r.get('tool_calls') for r in accepted)
 assert all(r['raw_response']['choices'][0]['message'].get('content')==r['content'] for r in accepted)
 assert fingerprint(inspect('vllm-qwen'))==m['model']['container_config_sha256']
 modeldir=Path(m['model']['checkpoint_path']);assert all((modeldir/f).stat().st_size==v['bytes'] and (modeldir/f).stat().st_mtime_ns==v['mtime_ns'] for f,v in m['model']['model_file_stats'].items())
 known={r['request_id'] for r in accepted};end=max(r['completed_at'] for r in accepted)
 others=[{'request_id':r['request_id'],'created_at':r['created_at'],'completed_at':r.get('completed_at'),'llm_call_count':r.get('llm_call_count')} for r in rows(ROOT/'data/chatbot/requests.jsonl') if m['created_at']<=r.get('created_at','')<=end and r['request_id'] not in known]
 p=subprocess.run(['docker','logs','--since',m['created_at'],'--until',end,'vllm-qwen'],capture_output=True,text=True,timeout=30);access=[s for s in (p.stdout+'\n'+p.stderr).splitlines() if 'POST /v1/chat/completions HTTP/' in s]
 dump(OUT/'server_access_audit.json',{'since':m['created_at'],'until_fixed_last_own_response':end,'access_count':len(access),'own_generation_count':len(accepted),'unattributed_generation_posts':len(access)-len(accepted),'other_app_records':others,'access_lines':access,'shared_server':'다른 API 이용은 중지하지 않음. 접근로그 건수만으로 서버 단독 사용을 보장하지 않음.','limitation':'사례ID가 없는접근로그라 외부요청과의경합은 정밀분리불가. 서버재시작·캐시초기화없음.'})
 audit={'at':datetime.now(timezone.utc).isoformat(),'evaluation_eligible':len(data),'preserved_harness_excluded':len(raw)-len(data),'warmup_calls':1,'ui_calls':4,'own_total_generation_calls':len(accepted),'one_generation_per_message':True,'tools_absent':True,'canonical_messages_reconstructed':all(reconstruction),'gold_leak_in_system':False,'source_checks':dict(Counter(x['mechanical_source_valid'] for x in source_audit)),'source_semantics_automatically_verified':False,'all_app_gold_model_settings_hashes_unchanged_during_runs':True,'checkpoint_sizes_mtimes_unchanged':True,'raw_model_content_unchanged':True,'old_records_preserved':True,'semantic_group_count':len(groups),'semantic_response_count':sum(len(g['members']) for g in groups),'semantic_review_sha256':sha(OUT/'manual_reviews.jsonl'),'review_method':'현재 AI가 전체 고유 생성·사용자맥락을 읽은 검토; 독립/사람/블라인드 검토 아님','old29_new_normal':sum(x['new_status']=='ok' for x in former),'old29_mapping_correct':sum(x['new_correct'] is True for x in former),'old29_meaning_pass':sum(x['new_explanation']=='통과' for x in former),'old29_evidence_support_pass':sum(x['new_evidence_support']=='통과' for x in former),'external_generation_posts':len(access)-len(accepted),'automatic_generation_retries':0,'output_repairs_or_gold_corrections':0}
 dump(OUT/'final_audit.json',audit);print(json.dumps(audit,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
