"""One new real vLLM message + read-only replay of saved regression conversations."""
import asyncio,json,sys
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend import chat_flow
OUT=ROOT/'evaluation/results/chat-fixes-20261102-01'
async def main():
 if (OUT/'browser_dispatch.json').exists():raise RuntimeError('저장된 실제 요청이 있어 자동 재생성하지 않음')
 gold=json.loads((OUT/'questions_gold.json').read_text())['browser_extra'];posts=[];checks={};errors=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--no-sandbox']);page=await browser.new_page(viewport={'width':1350,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:posts.append(r.post_data_json) if r.method=='POST' and r.url.endswith('/messages') else None)
  await page.add_init_script('''window.bodySamples=[];document.addEventListener('DOMContentLoaded',()=>{new MutationObserver(()=>{for(const card of document.querySelectorAll('.assistant[data-request-id]')){const r=chatState.turns.find(x=>x.request_id===card.dataset.requestId),body=card.querySelector('.chat-reply')?.textContent;if(body&&window.bodySamples.filter(x=>x.rid===r.request_id).at(-1)?.length!==body.length)window.bodySamples.push({rid:r.request_id,length:body.length,at:performance.now(),validated:r.analysis_validated});}}).observe(document.querySelector('#chat-messages'),{subtree:true,childList:true,characterData:true});});''')
  await page.goto('http://127.0.0.1:8080');await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('chatState.session&&!chatState.loading');q=page.locator('#chat-question')
  await q.fill('조합');await q.dispatch_event('compositionstart');await q.dispatch_event('keydown',{'key':'Enter','isComposing':True,'keyCode':229});checks['ime_composition_no_post']=len(posts)==0;await q.dispatch_event('compositionend');await asyncio.sleep(.08)
  await q.fill(gold['question']);start=await page.evaluate('performance.now()')
  async with page.expect_response(lambda r:r.url.endswith('/messages') and r.request.method=='POST',timeout=240000) as resp:await q.press('Enter')
  accepted=await (await resp.value).json();sid=accepted['record']['session_id'];rid=accepted['record']['request_id'];accept_at=await page.evaluate('performance.now()');(OUT/'browser_dispatch.json').write_text(json.dumps({'accepted':accepted,'session_id':sid,'request_id':rid,'question':gold['question']},ensure_ascii=False,indent=2))
  await page.wait_for_function('!chatState.accepting');checks['enter_clears_on_acceptance']=await q.input_value()=='';await q.fill('이 문장은 다음 질문 초안입니다.')
  await page.wait_for_function('(rid)=>window.bodySamples.filter(x=>x.rid===rid).length>=2',arg=rid,polling=5,timeout=240000)
  samples=await page.evaluate('(rid)=>window.bodySamples.filter(x=>x.rid===rid)',rid);await page.locator('#chat-cancel').click();await page.wait_for_function('!chatState.pending',timeout=240000);end=await page.evaluate('performance.now()');record=chat_flow.get(sid,rid)
  checks.update(validated_before_body=all(x['validated'] for x in samples),body_really_grows=samples[-1]['length']>samples[0]['length'],draft_preserved=await q.input_value()=='이 문장은 다음 질문 초안입니다.',partial_preserved=bool(record['reply']),interrupted=record['status']=='interrupted',one_submission=len(posts)==1,can_submit_after_cancel=await page.locator('#chat-send').is_enabled())
  await page.screenshot(path=str(OUT/'ui_interrupted.png'),full_page=True);await page.reload();await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('chatState.session&&!chatState.loading');checks['refresh_partial_status_exact']=await page.evaluate('(x)=>chatState.turns.find(r=>r.request_id===x.rid)?.reply===x.reply&&chatState.turns.find(r=>r.request_id===x.rid)?.status===x.status',{'rid':rid,'reply':record['reply'],'status':record['status']})
  checks['refresh_new_draft_exact']=await q.input_value()=='이 문장은 다음 질문 초안입니다.'
  sessions=json.loads((OUT/'sessions.json').read_text())
  async def open_session(s):
   await page.evaluate('(s)=>loadChat(false,s)',s);await page.wait_for_function('(s)=>chatState.session===s&&!chatState.loading',arg=s)
  await open_session(sessions['switch']);checks['switch_stored_three_turns']=await page.locator('.chat-message.user').count()==3;await page.locator('.assistant').last.locator('details').first.evaluate('(e)=>e.open=true');await page.screenshot(path=str(OUT/'ui_task_switch.png'),full_page=True)
  await open_session(sessions['ungrounded']);checks['clarify_displayed']=await page.evaluate('chatState.turns[0].analysis.requests[0].intent_id')=='clarify';await page.screenshot(path=str(OUT/'ui_clarify.png'),full_page=True)
  await open_session(sessions['compound']);checks['four_no_false_outside_notice']=await page.evaluate('chatState.turns[0].request_states.length===4&&chatState.turns[0].request_states.every(x=>x.status==="valid")');await page.screenshot(path=str(OUT/'ui_compound.png'),full_page=True)
  await open_session(sessions['mixed']);checks['real_outside_notice_retained']='범위 밖 부분' in await page.locator('#chat-messages').inner_text()
  await open_session(sessions['outside']);checks['outside_no_ai_body']=await page.locator('.chat-reply').count()==0 and '지원하는 업무 의도를 찾지 못했습니다.' in await page.locator('#chat-messages').inner_text()
  original=json.loads((OUT/'reported_originals.json').read_text())[0];await open_session(original['session_id']);card=page.locator(f'.assistant[data-request-id="{original["request_id"]}"]');await card.locator('details').first.evaluate('(e)=>e.open=true');await card.get_by_text('실패한 분석 원문 · 파싱 결과 · 검증 사유 (미검증)',exact=True).click();checks['old_failure_audit_visible']='"relation": "new_task"' in await card.inner_text() and '새 작업은 기존 슬롯을 상속하지 않습니다.' in await card.inner_text();await card.scroll_into_view_if_needed();await page.screenshot(path=str(OUT/'ui_failure_audit.png'),full_page=True)
  checks['saved_views_no_generation']=len(posts)==1;checks['page_errors']=errors
  result={'checks':checks,'record':record,'body_samples':samples,'browser_accept_seconds':(accept_at-start)/1000,'browser_first_body_seconds':(samples[0]['at']-start)/1000,'browser_total_until_interrupted_seconds':(end-start)/1000,'timing_method':'DOM MutationObserver +5ms polling; 정확한 화면 첫 페인트 측정 아님','physical_ime':'OS IME 미검증; 실제 브라우저에서 합성 composition 이벤트 검사'}
  result['timing_method']='DOM MutationObserver +5ms polling; 정확한 화면 첫 페인트 측정 아님'
  (OUT/'browser_run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));await browser.close();assert all(v is True for k,v in checks.items() if k!='page_errors') and not errors,checks
if __name__=='__main__':asyncio.run(main())
