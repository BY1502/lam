"""Saved diagnostics and export verification only, zero generation calls."""
import json,urllib.request
from pathlib import Path
OUT=Path(__file__).resolve().parents[2]/'evaluation/results/chat-fixes-20261102-01'
def main():
 old=json.loads((OUT/'reported_originals.json').read_text())[0];sid=old['session_id'];rid=old['request_id'];url='http://127.0.0.1:8080/api/chat/sessions/'+sid
 with urllib.request.urlopen(url+'/messages/'+rid) as f:r=json.load(f)
 assert r['analysis_failure']['model_output']==old['analysis_stage']['content'] and r['analysis_failure']['parsed_analysis']['memory']['relation']=='new_task'
 with urllib.request.urlopen(url+'/export') as f:export=[json.loads(l) for l in f]
 item=next(x for x in export if x['request_id']==rid);assert item['record']['analysis_failure']['model_output']==old['analysis_stage']['content']
 (OUT/'legacy_failure_export.json').write_text(json.dumps(item,ensure_ascii=False,indent=2));(OUT/'legacy_failure_read_checks.json').write_text(json.dumps({'raw_visible_in_terminal_audit':True,'parsed_analysis_visible':True,'export_preserves_error':item['record']['status']=='error','validation_failed':not item['record']['analysis_failure']['validated'],'generation_calls':0},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
