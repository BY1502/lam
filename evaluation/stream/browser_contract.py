"""Deterministic browser input regressions with intercepted HTTP fixtures (no model calls).
Real vLLM/browser streaming is measured separately; these fixtures are not performance evidence.
"""
import asyncio,json,re,time
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright
OUT=Path('/home/yt/lam/evaluation/results/chatbot-stream-20261102-01')
async def main():
 sessions={};posts=[];checks={};errors=[];lost=set()
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True,args=['--no-sandbox']);page=await b.new_page(viewport={'width':1300,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
  async def route(req):
   url=urlparse(req.request.url);path=url.path;method=req.request.method
   async def reply(data,status=200):await req.fulfill(status=status,content_type='application/json',body=json.dumps(data,ensure_ascii=False))
   if path=='/api/chat/sessions' and method=='POST':
    sid=f'{len(sessions)+1:032x}';sessions[sid]=[];await reply({'session_id':sid});return
   if path=='/api/chat/sessions':await reply({'sessions':[{'session_id':s,'created_at':'2026-11-02T00:00:00Z','messages':len(v),'last_at':None} for s,v in sessions.items()]});return
   parts=path.split('/');sid=parts[4];rs=sessions[sid]
   if len(parts)==5:await reply({'session_id':sid,'turns':rs});return
   if parts[-1]=='messages' and method=='POST':
    body=req.request.post_data_json;posts.append({'session_id':sid,**body});r=next((r for r in rs if r['request_id']==body['request_id']),None);reused=r is not None
    if not r:
     r={'session_id':sid,'request_id':body['request_id'],'question':body['question'],'status':'pending','phase':'accepted','reply':'','analysis':None,'analysis_validated':False,'llm_call_count':0};rs.append(r)
    if body['question']=='전송 실패 원문' and body['request_id'] not in lost:lost.add(body['request_id']);await req.abort();return
    await asyncio.sleep(.15);await reply({'accepted':True,'reused':reused,'record':r},202);return
   rid=parts[6];r=next(r for r in rs if r['request_id']==rid)
   if parts[-1]=='events' and r['question']=='오류 상태 질문':
    async def settle():
     await asyncio.sleep(.4);r.update(status='error',phase='error',error_kind='source_error',error='부정 테스트 출처 오류',reply='')
    asyncio.create_task(settle());await req.abort();return
   if parts[-1]=='events':
    initial=json.loads(json.dumps(r));await asyncio.sleep(1.8)
    a={'requests':[{'interpreted_request':'테스트 목적','intent_id':'status.query','reason':'테스트','target_expressions':[],'time_expression':None,'conditions':[],'evidence':[{'turn_id':rid,'quote':r['question']}]}]}
    body='<img src=x onerror="window.fixtureXss=true">모의 설명'
    r.update(status='completed',phase='complete',reply=body,analysis=a,analysis_validated=True)
    ev=[{'type':'snapshot','seq':0,'record':initial},{'type':'analysis','seq':1,'analysis':a,'memory_snapshot':[],'mock_tools':{'version':'test-fixture','requests':[]},'request_states':[],'intent_labels':[],'timings':{}},{'type':'delta','seq':2,'text':body},{'type':'complete','seq':3,'record':r}]
    try:await req.fulfill(content_type='application/x-ndjson',body=''.join(json.dumps({**e,'session_id':sid,'request_id':rid},ensure_ascii=False)+'\n' for e in ev))
    except Exception:pass
    return
   await reply(r)
  await page.route('**/api/chat/**',route);await page.goto('http://127.0.0.1:8080');await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('chatState.session && !chatState.loading');q=page.locator('#chat-question')
  # Real keyboard default newline and the same form requestSubmit path.
  await q.fill('줄바꿈');await q.press('Shift+Enter');await q.type('둘째 줄');checks['shift_enter_newline']=await q.input_value()=='줄바꿈\n둘째 줄' and len(posts)==0
  await q.fill('   ');await q.press('Enter');await asyncio.sleep(.1);checks['blank_not_sent']=len(posts)==0
  await q.fill('조합 중');await q.dispatch_event('compositionstart');await q.dispatch_event('keydown',{'key':'Enter','code':'Enter','isComposing':True,'keyCode':229});checks['ime_enter_not_sent']=len(posts)==0;await q.dispatch_event('compositionend');await asyncio.sleep(.08)
  await q.fill('Enter 전송 질문');await q.press('Enter');await page.wait_for_function('chatState.pending');checks['cleared_on_acceptance']=await q.input_value()==''
  await q.fill('처리 중 새로 작성한 문장');await q.press('Enter');checks['duplicate_submission_blocked']=len(posts)==1
  await page.wait_for_function('!chatState.pending && !chatState.accepting');checks['draft_preserved_on_completion']=await q.input_value()=='처리 중 새로 작성한 문장';checks['safe_model_rendering']=not await page.evaluate('Boolean(window.fixtureXss)') and await page.locator('.chat-reply img').count()==0
  await q.fill('버튼 전송 질문');await page.locator('#chat-send').click();await page.wait_for_function('chatState.pending');await page.wait_for_function('!chatState.pending');checks['button_and_enter_same_path']=len(posts)==2 and all(len(x['request_id'])==32 for x in posts)
  await q.fill('전송 실패 원문');await q.press('Enter');await page.wait_for_function('!chatState.accepting');await page.locator('#chat-retry').wait_for(state='visible');checks['transport_failure_preserves_input']=await q.input_value()=='전송 실패 원문';await page.locator('#chat-retry').click();await page.wait_for_function('chatState.pending');await page.wait_for_function('!chatState.pending');checks['retry_same_id_one_stored_message']=posts[-1]['request_id']==posts[-2]['request_id'] and len(sessions[posts[-1]['session_id']])==3
  await q.fill('오류 상태 질문');await q.press('Enter');await page.wait_for_function('chatState.pending');await q.fill('오류 뒤 새로 쓰는 질문');await page.wait_for_function('!chatState.pending',timeout=15000);checks['stream_failure_polls_terminal_state']=await page.locator('#chat-send').is_enabled() and await q.input_value()=='오류 뒤 새로 쓰는 질문' and await page.locator('.assistant').last.locator('.chat-reply').count()==0
  sid=await page.evaluate('chatState.session');await q.fill('저장할 초안');await page.reload();await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('chatState.session && !chatState.loading');checks['refresh_restores_draft_and_transcript']=await q.input_value()=='저장할 초안' and await page.locator('.chat-message.user').count()==4
  await page.locator('#chat-new').click();await page.wait_for_function('(s)=>chatState.session!==s && !chatState.loading',arg=sid);checks['new_session_isolated']=await page.locator('.chat-message.user').count()==0 and await q.input_value()==''
  await page.screenshot(path=str(OUT/'browser_contract_final.png'),full_page=True);checks['page_errors']=errors
  (OUT/'browser_contract_final_checks.json').write_text(json.dumps({'kind':'intercepted HTTP fixtures; not real vLLM/physical IME','checks':checks,'posts':posts},ensure_ascii=False,indent=2));assert all(v is True for k,v in checks.items() if k!='page_errors') and not errors,checks
  await b.close()
asyncio.run(main())
