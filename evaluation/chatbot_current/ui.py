"""Four actual chatbot UI samples from the already frozen gold; DOM timing, not first paint."""
import json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright
OUT=ROOT/'evaluation/results/chatbot-eval-20261029-01'

def main():
 if not (OUT/'completion.json').exists():raise RuntimeError('먼저 414건 평가를 완료해야 함; UI 표본을 병렬 실행하지 않음')
 if (OUT/'ui_runs.jsonl').exists():raise RuntimeError('UI 생성 표본을 자동 반복하지 않음')
 gold={x['id']:x for x in json.loads((OUT/'questions_gold.json').read_text())['cases']};chosen=[('normal','C01-T1',True),('followup','C01-T2',False),('compound','B18',True),('outside','A15-4',True)]
 (OUT/'ui_sample_gold.json').write_text(json.dumps({'selection_from_pre_frozen_gold':True,'samples':chosen,'timing':'submit capture event performance.now → MutationObserver로 해당 assistant DOM 완성+pending=false를 확인한 시각. 첫페인트가 아님. resource.responseEnd는 브라우저 HTTP완료, DOM 확인은 microtask. 자동화 대기는50ms polling이나 타임스탬프는 observer 내부에서 기록.','streaming':False},ensure_ascii=False,indent=2))
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1320,'height':1000});posts=[];errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:posts.append(r.url) if r.method=='POST' and r.url.endswith('/messages') else None)
  page.goto('http://127.0.0.1:8080');page.get_by_role('tab',name='챗봇',exact=True).click();page.wait_for_function('chatState.session && !chatState.loading')
  page.evaluate('''() => {
    window.uiChatTimings=[];
    document.querySelector('#chat-form').addEventListener('submit',()=>window.uiChatTimings.push({submit:performance.now(),priorIds:[...document.querySelectorAll('.assistant[data-request-id]')].map(x=>x.dataset.requestId)}),true);
    new MutationObserver(()=>{
      const e=window.uiChatTimings.at(-1);if(!e||e.dom||chatState.pending)return;
      const card=[...document.querySelectorAll('.assistant[data-request-id]')].find(x=>!e.priorIds.includes(x.dataset.requestId));
      if(card&&card.querySelector('.chat-reply')?.textContent){e.dom=performance.now();e.request_id=card.dataset.requestId;const resources=performance.getEntriesByType('resource').filter(v=>v.name.endsWith('/messages'));const r=resources.at(-1);if(r){e.resource_start=r.startTime;e.response_end=r.responseEnd;e.resource_duration=r.duration;}}
    }).observe(document.querySelector('#chat-messages'),{childList:true,subtree:true});
  }''')
  for name,iid,new_session in chosen:
   if new_session:
    old=page.evaluate('chatState.session');page.locator('#chat-new').click();page.wait_for_function('(old)=>chatState.session!==old && !chatState.loading',arg=old)
   q=gold[iid]['question'];sid=page.evaluate('chatState.session');page.locator('#chat-question').fill(q)
   with page.expect_response(lambda r:r.url.endswith('/messages') and r.request.method=='POST',timeout=240000) as response:page.locator('#chat-send').click()
   record=response.value.json();page.wait_for_function('(id)=>window.uiChatTimings.some(x=>x.request_id===id && x.dom)',arg=record['request_id'],polling=50)
   t=page.evaluate('(id)=>window.uiChatTimings.find(x=>x.request_id===id)',record['request_id']);row={'ui_sample':name,'case_id':iid,'session_id':sid,'record':record,'observer_timing':t,'send_to_dom_seconds':(t['dom']-t['submit'])/1000,'http_end_to_dom_seconds':(t['dom']-t['response_end'])/1000 if t.get('response_end') else None,'method':'DOM MutationObserver microtask; first paint not measured; playwright waiting poll=50ms','actual_ui_call_count':1}
   with (OUT/'ui_runs.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush()
   page.screenshot(path=str(OUT/f'ui_{name}.png'),full_page=True);print(name,record['status'],row['send_to_dom_seconds'],flush=True)
  # Read the saved local report through the existing read-only endpoint; no generation.
  before=len(posts);response=page.request.get('http://127.0.0.1:8080/api/evaluations/chatbot-eval-20261029-01/report');read_check={'status':response.status,'generation_posts_before':before,'generation_posts_after':len(posts),'no_new_generation':len(posts)==before,'report_text_available':response.ok and bool(response.json().get('text')),'page_errors':errors};(OUT/'ui_checks.json').write_text(json.dumps(read_check,ensure_ascii=False,indent=2));browser.close()
if __name__=='__main__':main()
