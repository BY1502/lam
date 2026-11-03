"""Frozen 24x3 evaluation through the existing chat API. No app/prompt modifications."""
import asyncio,fcntl,hashlib,json,os,sqlite3,subprocess,time,urllib.request,urllib.error,uuid
from datetime import datetime,timezone
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'evaluation/results/compound-20261103-01';BASE='http://127.0.0.1:8080'
def now():return datetime.now(timezone.utc).isoformat()
def read(name):return [json.loads(l) for l in (OUT/name).read_text().splitlines()] if (OUT/name).exists() else []
def append(name,row):
 with (OUT/name).open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
def api(path,body=None,timeout=240):
 req=urllib.request.Request(BASE+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=timeout) as f:return json.load(f)
def stored(sid,rid):
 with sqlite3.connect('file:'+str(ROOT/'data/chatbot/sessions.sqlite3')+'?mode=ro',uri=True) as c:
  row=c.execute('SELECT record_json FROM turns WHERE request_id=? AND session_id=?',(rid,sid)).fetchone()
 return json.loads(row[0]) if row else None
def terminal(sid,rid):
 end=time.monotonic()+195
 while True:
  r=stored(sid,rid)
  if not r or r['status']!='pending':return r
  if time.monotonic()>end:return r
  time.sleep(.2)
def verify_hashes():
 for name,h in json.loads((OUT/'preservation_before.json').read_text())['code'].items():
  assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h,'고정 코드 변경: '+name
 assert hashlib.sha256((OUT/'questions_gold.json').read_bytes()).hexdigest()==(OUT/'questions_gold.sha256').read_text()

async def browser_message(sid,q,key):
 posts=[];errors=[];dispatch={};start=time.perf_counter();samples=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--no-sandbox']);page=await browser.new_page(viewport={'width':1350,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)))
  await page.add_init_script('sessionStorage.setItem("lam-chat-session-v1", '+json.dumps(sid)+');')
  await page.add_init_script('''window.evalBodies=[];document.addEventListener('DOMContentLoaded',()=>{new MutationObserver(()=>{for(const card of document.querySelectorAll('.assistant[data-request-id]')){const r=chatState.turns.find(x=>x.request_id===card.dataset.requestId),body=card.querySelector('.chat-reply')?.textContent;if(body&&window.evalBodies.filter(x=>x.rid===r.request_id).at(-1)?.length!==body.length)window.evalBodies.push({rid:r.request_id,length:body.length,at:performance.now(),validated:r.analysis_validated,status:r.status,body});}}).observe(document.querySelector('#chat-messages'),{subtree:true,childList:true,characterData:true});});''')
  def sent(req):
   if req.method=='POST' and req.url.endswith('/messages'):
    data=req.post_data_json;posts.append(data);dispatch.update(request_id=data['request_id'],session_id=sid,question=q,key=key,at=now(),channel='browser');append('dispatches.jsonl',dict(dispatch))
  page.on('request',sent)
  await page.goto(BASE);await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('(s)=>chatState.session===s&&!chatState.loading',arg=sid)
  await page.locator('#chat-question').fill(q);ui_start=await page.evaluate('performance.now()');start=time.perf_counter()
  async with page.expect_response(lambda r:r.url.endswith('/messages') and r.request.method=='POST',timeout=240000) as response:await page.locator('#chat-question').press('Enter')
  accepted=await (await response.value).json();rid=accepted['record']['request_id'];accept=time.perf_counter()-start
  await page.wait_for_function('(id)=>chatState.turns.some(r=>r.request_id===id&&r.status!=="pending")',arg=rid,timeout=240000);ui_end=await page.evaluate('performance.now()');r=stored(sid,rid);samples=await page.evaluate('(id)=>window.evalBodies.filter(x=>x.rid===id)',rid);shown=await page.evaluate('(id)=>chatState.turns.find(r=>r.request_id===id).reply',rid)
  await page.screenshot(path=str(OUT/f'ui_{key}.png'),full_page=True);await page.reload();await page.get_by_role('tab',name='챗봇',exact=True).click();await page.wait_for_function('(s)=>chatState.session===s&&!chatState.loading',arg=sid);restored=await page.evaluate('(id)=>chatState.turns.find(r=>r.request_id===id)',rid)
  result={'key':key,'request_id':rid,'session_id':sid,'record_status':r['status'],'first_body_dom_seconds':(samples[0]['at']-ui_start)/1000 if samples else None,'total_dom_seconds':(ui_end-ui_start)/1000,'body_samples':samples,'checks':{'body_increased_multiple_times_before_complete':len([x for x in samples if x['status']=='pending'])>=2,'validated_before_body':all(x['validated'] for x in samples),'final_body_exact':shown==(r.get('reply') or ''),'refresh_body_exact':restored['reply']==r.get('reply'),'refresh_status_exact':restored['status']==r['status'],'no_resubmit_on_read':len(posts)==1,'no_page_errors':not errors},'page_errors':errors,'method':'실제 vLLM 스트림 DOM MutationObserver; 완료 조건5ms 기본 polling이 아닌 Playwright rAF 대기. 정확한 첫 paint 아님.'};append('browser_checks.jsonl',result);await browser.close()
  return {'accepted':accepted,'accept_http_seconds':accept,'client_total_seconds':time.perf_counter()-start,'record':r,'events':[],'browser_key':key,'ui_completed_seconds':result['total_dom_seconds'],'ui_first_body_seconds':result['first_body_dom_seconds']}

def send(sid,q,key,use_browser=False):
 previous=next((r for r in read('dispatches.jsonl') if r['key']==key),None)
 if previous:
  r=terminal(previous['session_id'],previous['request_id'])
  return {'record':r,'accepted':None,'events':[],'resumed_from_saved_request':True,'accept_http_seconds':None,'client_total_seconds':None}
 if use_browser:return asyncio.run(browser_message(sid,q,key))
 rid=uuid.uuid4().hex;append('dispatches.jsonl',{'key':key,'session_id':sid,'request_id':rid,'question':q,'at':now(),'channel':'api'});start=time.perf_counter();events=[];errors=[];accepted=None
 try:
  accepted=api(f'/api/chat/sessions/{sid}/messages',{'question':q,'request_id':rid});accept=time.perf_counter()-start
  with urllib.request.urlopen(BASE+f'/api/chat/sessions/{sid}/messages/{rid}/events',timeout=240) as f:
   for line in f:events.append({'received_seconds':time.perf_counter()-start,**json.loads(line)})
 except Exception as e:errors.append({'type':type(e).__name__,'message':str(e),'at':now()});accept=None
 r=terminal(sid,rid)
 return {'accepted':accepted,'accept_http_seconds':accept,'client_total_seconds':time.perf_counter()-start,'record':r,'events':events,'client_errors':errors}

def main():
 lock=(OUT/'runner.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);verify_hashes();gold=json.loads((OUT/'questions_gold.json').read_text());sessions=json.loads((OUT/'sessions.json').read_text()) if (OUT/'sessions.json').exists() else {};done={r['key'] for r in read('runs.jsonl')}
 if not (OUT/'manifest.json').exists():
  state=json.loads(subprocess.check_output(['docker','inspect','vllm-qwen'],text=True))[0]
  with urllib.request.urlopen('http://127.0.0.1:8001/version') as f:version=json.load(f)
  probes=api('/api/backends');v=next(x for x in probes if x['id']=='vllm_qwen');assert v['can_run'],v
  (OUT/'manifest.json').write_text(json.dumps({'created_at':now(),'engine':version,'model_probe':v,'image':state['Config']['Image'],'image_id':state['Image'],'model_command':state['Config']['Cmd'],'device_requests':state['HostConfig']['DeviceRequests'],'code_hashes':json.loads((OUT/'preservation_before.json').read_text())['code'],'dataset_sha256':hashlib.sha256((OUT/'questions_gold.json').read_bytes()).hexdigest(),'inference':{'think':False,'temperature':0,'seed':20261012,'analysis_output_limit':4096,'response_output_limit':1536},'cache':'캐시 초기화·모델 재적재 없음. 별도 준비 요청1건을 먼저 기록. 다른 API 사용자 중지/제어 안 함.','sequence':'3 repetitions, each1..24 sequential; browser samples10/13/22 repeat1; no app/model changes'},ensure_ascii=False,indent=2))
 if not read('warmup.jsonl'):
  sid=api('/api/chat/sessions',{})['session_id'];result=send(sid,gold['warmup_question'],'warmup');append('warmup.jsonl',{'key':'warmup','question':gold['warmup_question'],**result});print('warmup',result['record']['status'] if result['record'] else 'not_saved',flush=True)
 for repeat in range(1,4):
  for case in gold['items']:
   verify_hashes();n=case['id'];sk=f'{n:02d}-r{repeat}'
   if sk not in sessions:sessions[sk]=api('/api/chat/sessions',{})['session_id'];(OUT/'sessions.json').write_text(json.dumps(sessions))
   sid=sessions[sk]
   for phase,q in [('preparation',case['preparation']),('evaluation',case['question'])]:
    if q is None:continue
    key=sk+'-'+phase
    if key in done:continue
    result=send(sid,q,key,use_browser=(n in (10,13,22) and repeat==1 and phase=='evaluation'));r=result['record'];row={'key':key,'question_id':n,'repeat':repeat,'phase':phase,'session_id':sid,'question':q,'group':case['group'],**result};append('runs.jsonl',row);done.add(key)
    print(key,r['status'] if r else 'not_saved',(r.get('timings') or {}).get('total_seconds') if r else '',[x['intent_id'] for x in (r.get('analysis') or {}).get('requests',[])] if r else [],flush=True)
 if not read('control.jsonl'):
  sid=api('/api/chat/sessions',{})['session_id'];result=send(sid,gold['outside_control_question'],'outside-control');append('control.jsonl',{'key':'outside-control','question':gold['outside_control_question'],**result})
 verify_hashes();(OUT/'completion.json').write_text(json.dumps({'at':now(),'messages':len(read('runs.jsonl')),'evaluation_cases':sum(r['phase']=='evaluation' for r in read('runs.jsonl')),'hashes_unchanged':True}));print('complete',flush=True)
if __name__=='__main__':main()
