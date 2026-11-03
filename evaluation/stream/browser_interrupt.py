"""An additional pre-frozen interruption scenario; original harness failure remains preserved."""
import asyncio,json,sys,hashlib
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend import chat_flow
OUT=ROOT/'evaluation/results/chatbot-stream-20261102-01'
async def main():
 if (OUT/'browser_interrupt_run.json').exists():raise RuntimeError('완료/실패 원본이 있어 모델 재호출 안 함')
 gold=json.loads((OUT/'extra_verification_gold.json').read_text());posts=[];errors=[];checks={}
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True,args=['--no-sandbox']);page=await b.new_page(viewport={'width':1350,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:posts.append(r.post_data_json) if r.method=='POST' and r.url.endswith('/messages') else None)
  await page.add_init_script('''window.bodyObservations=[];document.addEventListener('DOMContentLoaded',()=>{new MutationObserver(()=>{for(const c of document.querySelectorAll('.assistant[data-request-id]')){const r=chatState.turns.find(x=>x.request_id===c.dataset.requestId),body=c.querySelector('.chat-reply')?.textContent;if(body&&!window.bodyObservations.some(x=>x.request_id===r.request_id))window.bodyObservations.push({request_id:r.request_id,at:performance.now(),validated:r.analysis_validated,text:body});}}).observe(document.querySelector('#chat-messages'),{subtree:true,childList:true,characterData:true});});''')
  await page.goto('http://127.0.0.1:8080');await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('chatState.session && !chatState.loading');q=page.locator('#chat-question');sid=await page.evaluate('chatState.session')
  # Synthetic composition events in a real browser; physical OS IME is not available headlessly.
  await q.fill('조합');await q.dispatch_event('compositionstart');await q.dispatch_event('keydown',{'key':'Enter','isComposing':True,'keyCode':229});checks['ime_event_no_post']=len(posts)==0;await q.dispatch_event('compositionend');await asyncio.sleep(.08)
  await q.fill(gold['question']);start=await page.evaluate('performance.now()')
  async with page.expect_response(lambda r:r.url.endswith('/messages') and r.request.method=='POST',timeout=240000) as resp:await q.press('Enter')
  acceptance=await (await resp.value).json();rid=acceptance['record']['request_id'];accepted_at=await page.evaluate('performance.now()')
  (OUT/'browser_interrupt_dispatch.json').write_text(json.dumps({'session_id':sid,'request_id':rid,'accepted':acceptance,'question':gold['question']},ensure_ascii=False,indent=2))
  await page.wait_for_function('chatState.pending && !chatState.accepting');checks['cleared_after_accept']=await q.input_value()=='';await q.fill('응답 중 새로 작성하는 다음 질문')
  await page.wait_for_function('(id)=>window.bodyObservations.some(x=>x.request_id===id)',arg=rid,polling=5,timeout=240000);first=await page.evaluate('(id)=>window.bodyObservations.find(x=>x.request_id===id)',rid);await page.evaluate('document.querySelector("#chat-cancel").click()');await page.wait_for_function('!chatState.pending && !chatState.accepting',timeout=240000);end=await page.evaluate('performance.now()');record=chat_flow.get(sid,rid)
  checks.update(validated_before_first_body=first['validated'],partial_saved=bool(record['reply']),status_interrupted=record['status']=='interrupted',new_draft_preserved=await q.input_value()=='응답 중 새로 작성하는 다음 질문',input_send_enabled=await page.locator('#chat-send').is_enabled(),no_corrupt_korean='\ufffd' not in record['reply'],one_submission=len(posts)==1)
  await page.locator('.assistant').last.locator('summary').click();await page.screenshot(path=str(OUT/'browser_partial_interrupt.png'),full_page=True)
  await page.reload();await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('chatState.session && !chatState.loading');checks['refresh_preserves_interruption']=await page.evaluate('(id)=>chatState.turns.find(r=>r.request_id===id)?.status',rid)=='interrupted';checks['refresh_preserves_partial']=await page.evaluate('(id)=>chatState.turns.find(r=>r.request_id===id)?.reply',rid)==record['reply'];checks['refresh_no_generation']=len(posts)==1
  async with page.expect_download() as dl:await page.locator('#chat-export').click()
  await (await dl.value).save_as(OUT/'browser_interrupt_export.jsonl');exported=json.loads((OUT/'browser_interrupt_export.jsonl').read_text().splitlines()[-1]);checks['export_original_unreviewed']=exported['request_id']==rid and exported['review_status']=='unreviewed' and not exported['labels_are_gold']
  # Open the prior harness-interrupted saved conversation read-only.
  old=json.loads((OUT/'browser_harness_interrupted_record.json').read_text())['record'];await page.select_option('#chat-sessions',old['session_id']);await page.wait_for_function('(sid)=>chatState.session===sid&&!chatState.loading',arg=old['session_id']);checks['saved_other_session_isolated']=await page.locator('.chat-message.user').count()==1 and await page.evaluate('chatState.turns[0].request_id')==old['request_id'];checks['saved_interrupted_read_no_generation']=len(posts)==1
  # Verify no AI body in a real stored out-of-scope conversation.
  sessions=json.loads((OUT/'sessions.json').read_text());await page.select_option('#chat-sessions',sessions['outside']);await page.wait_for_function('(sid)=>chatState.session===sid&&!chatState.loading',arg=sessions['outside']);checks['outside_no_ai_body']=await page.locator('.chat-reply').count()==0 and '지원하는 업무 의도를 찾지 못했습니다.' in await page.locator('#chat-messages').inner_text();await page.screenshot(path=str(OUT/'browser_outside.png'),full_page=True)
  checks['page_errors']=errors;result={'case':gold['id'],'record':record,'checks':checks,'browser_accept_seconds':(accepted_at-start)/1000,'browser_first_body_seconds':(first['at']-start)/1000,'browser_total_dom_seconds':(end-start)/1000,'timing':'DOM MutationObserver, not first paint; polling5ms','first_observation':first}
  (OUT/'browser_interrupt_run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));await b.close();print(json.dumps(checks,ensure_ascii=False))
asyncio.run(main())
