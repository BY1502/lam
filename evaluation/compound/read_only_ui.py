"""Review stored scope/clarification/error displays. All POSTs are blocked."""
import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
OUT=Path(__file__).resolve().parents[2]/'evaluation/results/compound-20261103-01'
async def main():
 rows=[json.loads(l) for l in (OUT/'runs.jsonl').read_text().splitlines()];chosen=[next(x for x in rows if x['question_id']==n and x['repeat']==1 and x['phase']=='evaluation') for n in [14,23,24]];control=json.loads((OUT/'control.jsonl').read_text().splitlines()[0]);checks=[];posts=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--no-sandbox']);page=await browser.new_page(viewport={'width':1350,'height':1050});await page.add_init_script('sessionStorage.setItem("lam-chat-session-v1", '+json.dumps(chosen[0]['session_id'])+');')
  async def guard(route):
   if route.request.method=='POST':posts.append(route.request.url);await route.abort()
   else:await route.continue_()
  await page.route('**/api/**',guard);await page.goto('http://127.0.0.1:8080');await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('chatState.session&&!chatState.loading')
  for x in [*chosen,control]:
   r=x['record'];sid=r['session_id'];await page.evaluate('(s)=>loadChat(false,s)',sid);await page.wait_for_function('(s)=>chatState.session===s&&!chatState.loading',arg=sid);shown=await page.evaluate('(id)=>chatState.turns.find(r=>r.request_id===id)',r['request_id']);case=x.get('question_id','outside-control');valid=shown['reply']==r.get('reply') and shown['status']==r['status']
   if case==14:valid &= '범위 밖 부분' in await page.locator('#chat-messages').inner_text() and '여행' not in (await page.locator('.chat-reply').last.inner_text())
   if case==23:valid &= all(q['intent_id']=='clarify' for q in shown['analysis']['requests'])
   if r['status']=='error':
    card=page.locator(f'.assistant[data-request-id="{r["request_id"]}"]');await card.locator('details').first.evaluate('(e)=>e.open=true');await card.get_by_text('실패한 분석 원문 · 파싱 결과 · 검증 사유 (미검증)',exact=True).click();valid &= bool(shown.get('analysis_failure'))
   if case=='outside-control':valid &= await page.locator('.chat-reply').count()==0 and shown['response_call_count']==0
   await page.screenshot(path=str(OUT/f'ui_readonly_{case}.png'),full_page=True);checks.append({'case':case,'request_id':r['request_id'],'saved_content_state_exact':bool(valid)})
  result={'checks':checks,'attempted_posts':posts,'generation_calls_added':0,'method':'이 API브라우저는 모든POST차단. 기존완료행을 GET으로만 열람.'};(OUT/'read_only_ui.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));await browser.close();assert all(c['saved_content_state_exact'] for c in checks) and not posts,result
if __name__=='__main__':asyncio.run(main())
