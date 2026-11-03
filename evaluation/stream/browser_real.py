"""Two pre-frozen real vLLM browser samples. Never replays a failed generation."""
import asyncio,json,time,sys,hashlib
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend import chat_flow
OUT=ROOT/'evaluation/results/chatbot-stream-20261102-01'
async def main():
 if (OUT/'browser_real_runs.jsonl').exists():raise RuntimeError('원본 존재. 생성 표본을 자동 재실행하지 않습니다.')
 gold=json.loads((OUT/'questions_gold.json').read_text())['extra_browser_samples'];checks={};posts=[];errors=[]
 (OUT/'browser_real_code_hashes.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for d in ['backend','frontend','prompts','settings'] for p in (ROOT/d).rglob('*') if p.is_file() and p.suffix in ['.py','.js','.css','.html','.md','.json']},indent=2))
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True,args=['--no-sandbox']);page=await b.new_page(viewport={'width':1350,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:posts.append(r.post_data_json) if r.method=='POST' and r.url.endswith('/messages') else None)
  await page.goto('http://127.0.0.1:8080');await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('chatState.session && !chatState.loading');q=page.locator('#chat-question')
  await page.evaluate('''()=>{window.realUiObservations=[];new MutationObserver(()=>{const cards=[...document.querySelectorAll('.assistant[data-request-id]')];for(const card of cards){const id=card.dataset.requestId;const record=chatState.turns.find(r=>r.request_id===id);const body=card.querySelector('.chat-reply')?.textContent;if(body&&!window.realUiObservations.some(x=>x.request_id===id)){window.realUiObservations.push({request_id:id,first_dom:performance.now(),analysis_validated:!!record?.analysis_validated,body});}}}).observe(document.querySelector('#chat-messages'),{childList:true,subtree:true,characterData:true});}''')
  await q.fill('한글 조합');await q.dispatch_event('compositionstart');await q.dispatch_event('keydown',{'key':'Enter','code':'Enter','isComposing':True,'keyCode':229});checks['composition_enter_no_post']=len(posts)==0;await q.dispatch_event('compositionend');await asyncio.sleep(.08)
  for n,case in enumerate(gold):
   if n:
    sid=await page.evaluate('chatState.session');await page.locator('#chat-new').click();await page.wait_for_function('(s)=>chatState.session!==s && !chatState.loading',arg=sid)
   sid=await page.evaluate('chatState.session');await q.fill(case['question']);started=await page.evaluate('performance.now()')
   async with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/messages'),timeout=240000) as response:await q.press('Enter')
   accepted=await (await response.value).json();rid=accepted['record']['request_id'];accept_at=await page.evaluate('performance.now()');await page.wait_for_function('!chatState.accepting')
   checks[case['id']+'_cleared_on_accept']=await q.input_value()==''
   await q.fill('새로 작성 중인 다음 질문');await q.press('Enter');checks[case['id']+'_one_post']=len(posts)==n+1
   await page.wait_for_function('(id)=>window.realUiObservations.some(x=>x.request_id===id)',arg=rid,polling=10,timeout=240000)
   if case['id']=='browser_cancel':
    await page.evaluate('document.querySelector("#chat-cancel").click()')
   await page.wait_for_function('!chatState.pending && !chatState.accepting',timeout=240000)
   final_at=await page.evaluate('performance.now()');record=chat_flow.get(sid,rid);obs=await page.evaluate('(id)=>window.realUiObservations.find(x=>x.request_id===id)',rid)
   row={'case_id':case['id'],'session_id':sid,'request_id':rid,'question':case['question'],'expected':case['expected'],'record':record,'browser_accept_seconds':(accept_at-started)/1000,'browser_first_body_seconds':(obs['first_dom']-started)/1000,'browser_total_dom_seconds':(final_at-started)/1000,'first_body_observation':obs,'timing_method':'MutationObserver DOM text insertion, wait poll10ms; not browser first paint'}
   with (OUT/'browser_real_runs.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush()
   checks[case['id']+'_draft_preserved']=await q.input_value()=='새로 작성 중인 다음 질문';checks[case['id']+'_validated_before_body']=obs['analysis_validated'];checks[case['id']+'_not_corrupted']='\ufffd' not in record['reply'];checks[case['id']+'_terminal']=record['status']=='interrupted' if case['id']=='browser_cancel' else record['status']=='completed'
   await page.locator('.assistant').last.locator('summary').click();await page.screenshot(path=str(OUT/(case['id']+'.png')),full_page=True)
   # Browser refresh opens the same saved session and partial/final text, no model invocation.
   before=len(posts);await page.reload();await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('chatState.session && !chatState.loading');checks[case['id']+'_refresh_same_session']=await page.evaluate('chatState.session')==sid;checks[case['id']+'_refresh_no_generation']=len(posts)==before
   restored=await page.evaluate('(id)=>chatState.turns.find(x=>x.request_id===id)?.reply',rid);checks[case['id']+'_restored_exact_text']=restored==record['reply']
   async with page.expect_download() as download:await page.locator('#chat-export').click()
   await (await download.value).save_as(OUT/(case['id']+'_export.jsonl'));exported=json.loads((OUT/(case['id']+'_export.jsonl')).read_text().splitlines()[-1]);checks[case['id']+'_export_unreviewed']=exported['review_status']=='unreviewed' and exported['labels_are_gold'] is False and exported['request_id']==rid
   print(case['id'],record['status'],round(row['browser_first_body_seconds'],3),round(row['browser_total_dom_seconds'],3),flush=True)
  checks['page_errors']=errors;(OUT/'browser_real_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2));await b.close()
asyncio.run(main())
