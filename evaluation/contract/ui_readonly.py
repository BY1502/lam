"""Read saved sessions and expand source details; zero generation requests."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright
OUT=ROOT/'evaluation/results/chatbot-contract-20261029-01'
def main():
 rs=[json.loads(l) for l in (OUT/'ui_runs.jsonl').read_text().splitlines()];follow=next(r for r in rs if r['ui_sample']=='followup');old=json.loads((ROOT/'evaluation/results/chatbot-eval-20261029-01/runs.jsonl').read_text().splitlines()[0]);posts=[];errors=[]
 with sync_playwright() as p:
  b=p.chromium.launch(headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1320,'height':1000});page.on('request',lambda r:posts.append(r.url) if r.method=='POST' and r.url.endswith('/messages') else None);page.on('pageerror',lambda e:errors.append(str(e)))
  page.add_init_script('sessionStorage.setItem("lam-chat-session-v1",'+json.dumps(follow['session_id'])+')');page.goto('http://127.0.0.1:8080');page.get_by_role('tab',name='챗봇',exact=True).click();page.wait_for_function('chatState.session && !chatState.loading');card=page.locator('.assistant').last;card.locator('summary').click();button=card.get_by_role('button',name=__import__('re').compile('이전 사용자 턴')).first;button.click();prior_focus=page.evaluate('document.activeElement?.dataset.turnId');page.screenshot(path=str(OUT/'ui_followup_expanded.png'),full_page=True)
  page.evaluate('(sid)=>{chatState.session=sid;chatState.turns=[];sessionStorage.setItem("lam-chat-session-v1",sid)}',old['session_id']);page.evaluate('loadChat()');page.wait_for_function('!chatState.loading');page.locator('.assistant').first.locator('summary').click();page.screenshot(path=str(OUT/'ui_old_schema_expanded.png'),full_page=True)
  (OUT/'ui_readonly_checks.json').write_text(json.dumps({'previous_source_focused_turn_id':prior_focus,'expected_previous_turn_id':rs[1]['record']['request_id'],'prior_focus_matches':prior_focus==rs[1]['record']['request_id'],'generation_posts':len(posts),'page_errors':errors},ensure_ascii=False,indent=2));b.close()
if __name__=='__main__':main()
