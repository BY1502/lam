// Server records are authoritative. Drafts/outboxes are isolated by session; no business execution.
const chatState={session:null,pending:false,accepting:false,turns:[],loading:false,view:0,stream:null,composing:false,compositionEnd:-Infinity,draftRevision:0,offset:0};
const sessionKey='lam-chat-session-v1';
const terminalChat=r=>r.status!=='pending';
const draftKey=s=>'lam-chat-draft-v3:'+s, outboxKey=s=>'lam-chat-outbox-v3:'+s;
function stored(key,fallback=''){try{return localStorage.getItem(key)||fallback;}catch{return fallback;}}
function persist(key,value){try{if(value===null)localStorage.removeItem(key);else localStorage.setItem(key,value);}catch{}}
function outbox(sid){try{return JSON.parse(stored(outboxKey(sid),'null'));}catch{return null;}}
function setTab(name){for(const v of ['test','chat']){const a=v===name;$('tab-'+v).setAttribute('aria-selected',String(a));$('tab-'+v).tabIndex=a?0:-1;$('panel-'+v).hidden=!a;}if(name==='chat'&&!chatState.session&&!chatState.loading)loadChat();}
for(const n of ['test','chat']){$('tab-'+n).onclick=()=>setTab(n);$('tab-'+n).onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const v=n==='test'?'chat':'test';setTab(v);$('tab-'+v).focus();}};}
const phaseNames={accepted:'대기',intent:'의도 분석 중',analysis_validated:'의도 검증 완료',response:'응답 중',complete:'완료',error:'오류',interrupted:'중단'};
function chatBusy(){const busy=chatState.pending||chatState.accepting||chatState.loading;$('chat-send').disabled=busy;$('chat-cancel').disabled=!chatState.pending;$('chat-new').disabled=chatState.loading;$('chat-busy').textContent=chatState.loading?'대화 불러오는 중':chatState.accepting?'사용자 메시지 저장 확인 중':chatState.pending?'분석/응답 처리 중 · 입력창에 다음 질문을 작성할 수 있습니다.':'';$('chat-retry').hidden=!outbox(chatState.session);$('chat-export').hidden=!chatState.session;if(chatState.session)$('chat-export').href='/api/chat/sessions/'+chatState.session+'/export';}
function chatUser(question,turnId){const c=text($('chat-messages'),'article','');c.className='chat-message user';c.dataset.turnId=turnId;c.tabIndex=-1;text(c,'strong','사용자');text(c,'p',question);return c;}
function showSource(parent,e,r){const b=text(parent,'blockquote','');text(b,'p',e.quote);const button=text(b,'button',`${e.turn_id===r.request_id?'현재':'이전'} 사용자 턴 ${e.turn_id}`);button.type='button';button.onclick=()=>{const src=[...document.querySelectorAll('.chat-message.user')].find(x=>x.dataset.turnId===e.turn_id);if(src){src.scrollIntoView({block:'center',behavior:'smooth'});src.focus({preventScroll:true});}else text(b,'small','전달 범위 밖의 이전 턴입니다. 저장 대화 원문에서 확인하세요.');};}
function chatResponse(r){
 const c=text($('chat-messages'),'article','');c.className='chat-message assistant'+(['error','interrupted','chat_error'].includes(r.status)?' error-message':'');c.dataset.requestId=r.request_id;
 text(c,'strong',r.reply?'AI · 의도 설명':'처리 상태');text(c,'p',phaseNames[r.phase]||({ok:'완료',chat_error:'오류',pending:'처리 중'}[r.status])||r.status).className='chat-stage';
 if(r.reply)text(c,'p',r.reply).className='chat-reply';
 if(r.status_message)text(c,'p',r.status_message).className='notice';
 if(r.request_states?.some(x=>x.status==='out_of_scope')&&!r.status_message)text(c,'p','범위 밖 부분: 지원하는 업무 의도를 찾지 못했습니다.').className='notice';
 if(r.error)text(c,'p',(r.error_kind||'오류')+' · '+r.error).className='error';
 const t=r.timings||{};text(c,'p',`${r.model||r.backend||'vLLM Qwen'} · think=${JSON.stringify(r.think_requested??false)} · ${r.chat_prompt_version||''} · 호출 ${r.llm_call_count??'미기록'}회 · 분석 ${t.analysis_seconds?.toFixed(3)??'미측정'}초 · 첫 조각 ${t.first_fragment_seconds?.toFixed(3)??'미측정'}초 · 전체 ${(t.total_seconds??r.elapsed_seconds)?.toFixed(3)??(r.status==='pending'?'처리 중':'미측정')}초`).className='chat-meta';
 const d=text(c,'details','');text(d,'summary','의도 · 활성 조건 · 원문 출처 · 모의 도구 · 원본 기록');
 for(const [i,q] of (r.analysis?.requests||[]).entries()){
  text(d,'h3',`요청 ${i+1}: ${q.interpreted_request}`);text(d,'p',`${r.intent_labels?.[i]?.name||''} (${q.intent_id}) · ${q.reason}`);text(d,'p','정리한 대상·기간 (해석): '+((q.target_expressions||[]).join(' / ')||'명시 없음')+' · '+(q.time_expression||'명시 없음'));text(d,'p','정리한 조건 (해석): '+((q.conditions||[]).join(' / ')||'없음'));
  if(q.missing_information?.length)text(d,'p','부족한 정보: '+q.missing_information.join(' / '));if(q.clarification_question)text(d,'p','확인 질문: '+q.clarification_question);
  if(q.evidence){text(d,'h4','원문 근거 — 실제 사용자 턴');for(const e of q.evidence)showSource(d,e,r);}else{text(d,'p','이전 계약 기록 — 원문 그대로 보존');for(const e of q.context_used||[])showSource(d,e,r);}
 }
 if(r.memory_snapshot){text(d,'h3','활성 맥락 — 사용자 발언을 정리한 모델 해석');text(d,'p','시스템 관측·조회 결과·정답이 아닙니다. 이전 값은 과거 턴 원문에 남습니다.');for(const f of r.memory_snapshot){text(d,'p',`${f.slot}: ${f.value}`);for(const e of f.evidence||[])showSource(d,e,r);}}
 if(r.execution_facts)text(d,'p','시스템 수행 사실: 실제 조회 결과·시스템이 생성한 구체적 제안·승인·적용 없음.');
 if(r.mock_tools){text(d,'h3','모의 도구 구성 · 실제 실행 안 함');text(d,'p',r.mock_tools.version+' · 실제 도구 호출 0회');for(const v of r.mock_tools.requests){text(d,'p',`요청 ${v.request_index+1}: `+(v.candidates.map(x=>`${x.name} (${x.id}) · 미구현/실행 불가${x.approval_required?' · 승인 필요·미승인':''}`).join(' / ')||'연결 후보 없음'));}}
 if(r.validation_checks)text(d,'p',`검사: 형식 ${r.validation_checks.format} · 출처 ${r.validation_checks.source} · 의미 정확성은 자동 검증하지 않음`);
 if(r.analysis_failure){const failed=text(d,'details','');text(failed,'summary','실패한 분석 원문 · 파싱 결과 · 검증 사유 (미검증)');text(failed,'p',r.analysis_failure.notice);text(failed,'pre',r.analysis_failure.model_output||'출력 없음');text(failed,'pre',JSON.stringify({parse_status:r.analysis_failure.parse_status,parsed_analysis:r.analysis_failure.parsed_analysis,parse_error:r.analysis_failure.parse_error,validation_failure:r.analysis_failure.validation_failure},null,2));}
 if(r.context_limits?.history_truncated)text(d,'p','일부 이전 맥락은 길이 제한으로 제외됐습니다. 제외 내용을 복원하지 않습니다.');text(d,'pre',JSON.stringify(r,null,2));
 if(['error','interrupted','chat_error'].includes(r.status)){const restore=text(c,'button','이 질문을 입력창으로 복사');restore.type='button';restore.onclick=()=>{if($('chat-question').value&&!confirm('작성 중인 질문을 이 원문으로 바꿀까요?'))return;$('chat-question').value=r.question;saveDraft();$('chat-question').focus();};}
 return c;
}
function renderChat(){const area=$('chat-messages');area.replaceChildren();if(!chatState.turns.length)text(area,'p','요청을 분석·분류하고 모의 도구 구성을 설명합니다. 실제 업무는 수행하지 않습니다.');for(const r of chatState.turns){chatUser(r.question,r.request_id);chatResponse(r);}$('chat-session').textContent=chatState.session?'대화 세션 '+chatState.session:'';}
function mergeRecord(r){if(r.session_id!==chatState.session)return;const i=chatState.turns.findIndex(x=>x.request_id===r.request_id);if(i<0)chatState.turns.push(r);else chatState.turns[i]=r;chatState.pending=chatState.turns.some(x=>x.status==='pending');renderChat();chatBusy();}
function saveDraft(){chatState.draftRevision++;if(chatState.session)persist(draftKey(chatState.session),$('chat-question').value);}
$('chat-question').addEventListener('input',saveDraft);
async function refreshSessionList(append=false){try{const offset=append?chatState.offset:0;const data=await api(`/api/chat/sessions?limit=50&offset=${offset}`);const select=$('chat-sessions');if(!append)select.replaceChildren();for(const s of data.sessions){if([...select.options].some(o=>o.value===s.session_id))continue;const o=document.createElement('option');o.value=s.session_id;o.textContent=`${s.session_id.slice(0,8)} · ${s.messages}개 · ${new Date(s.last_at||s.created_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})}`;select.append(o);}chatState.offset=offset+data.sessions.length;if(chatState.session){if(![...select.options].some(o=>o.value===chatState.session)){const o=new Option(chatState.session.slice(0,8)+' · 선택 대화',chatState.session);select.prepend(o);}select.value=chatState.session;}$('chat-more').disabled=data.sessions.length<50;}catch(e){$('chat-error').textContent=e.message;}}
async function loadChat(fresh=false,selected=null){
 if(chatState.loading)return;if(chatState.session)persist(draftKey(chatState.session),$('chat-question').value);const view=++chatState.view;chatState.stream?.abort();chatState.stream=null;chatState.loading=true;chatState.accepting=false;chatState.pending=false;chatBusy();$('chat-error').textContent='';
 try{let sid=fresh?null:selected||sessionStorage.getItem(sessionKey);if(!sid)sid=(await api('/api/chat/sessions',{})).session_id;const data=await api('/api/chat/sessions/'+sid);if(view!==chatState.view)return;chatState.session=sid;sessionStorage.setItem(sessionKey,sid);chatState.turns=data.turns;chatState.draftRevision++;$('chat-question').value=stored(draftKey(sid));chatState.pending=data.turns.some(r=>r.status==='pending');renderChat();await refreshSessionList();const active=data.turns.find(r=>r.status==='pending');if(active)watch(sid,active.request_id,view);
 }catch(e){$('chat-error').textContent=e.message;}finally{if(view===chatState.view){chatState.loading=false;chatBusy();}}
}
async function watch(sid,rid,view){
 const control=new AbortController();chatState.stream=control;let seq=-1;
 try{
  const response=await fetch(`/api/chat/sessions/${sid}/messages/${rid}/events`,{signal:control.signal,cache:'no-store'});if(!response.ok)throw Error('응답 연결 실패');const reader=response.body.getReader();const decoder=new TextDecoder('utf-8',{fatal:true});let buffer='';
  function consume(line){if(!line.trim())return;const e=JSON.parse(line);if(view!==chatState.view||sid!==chatState.session||e.session_id!==sid||e.request_id!==rid||e.seq<=seq)return;seq=e.seq;if(e.type==='heartbeat')return;
   if(e.type==='snapshot'||e.type==='complete'||e.type==='error'){if(e.record)mergeRecord(e.record);return;}
   let r=chatState.turns.find(x=>x.request_id===rid);if(!r)return;
   if(e.type==='analysis'){r={...r,llm_call_count:e.llm_call_count??r.llm_call_count,analysis_call_count:e.analysis_call_count,response_call_count:e.response_call_count,analysis:e.analysis,analysis_validated:true,memory_snapshot:e.memory_snapshot,mock_tools:e.mock_tools,request_states:e.request_states,intent_labels:e.intent_labels,timings:e.timings,phase:'analysis_validated'};mergeRecord(r);}
   if(e.type==='status'){r.phase=e.phase;if(e.llm_call_count!==undefined){r.llm_call_count=e.llm_call_count;mergeRecord(r);}const card=document.querySelector(`.assistant[data-request-id="${rid}"]`);if(card)card.querySelector('.chat-stage').textContent=phaseNames[e.phase]||e.phase;}
   if(e.type==='delta'){if(!r.analysis_validated)throw Error('검증 전 본문 조각을 차단했습니다.');r.reply=(r.reply||'')+e.text;r.phase='response';const card=document.querySelector(`.assistant[data-request-id="${rid}"]`);if(card){let body=card.querySelector('.chat-reply');if(!body){card.querySelector('strong').textContent='AI · 의도 설명';body=text(card,'p','');body.className='chat-reply';}body.textContent=r.reply;}}
  }
  while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let n;while((n=buffer.indexOf('\n'))>=0){consume(buffer.slice(0,n));buffer=buffer.slice(n+1);}}buffer+=decoder.decode();if(buffer.trim())consume(buffer);
 }catch(e){if(view===chatState.view&&sid===chatState.session){$('chat-error').textContent=e.name==='AbortError'?'응답 연결이 중단되었습니다. 저장된 상태를 확인합니다.':e.message+' · 자동 재생성하지 않습니다.';}}
 finally{if(view===chatState.view&&sid===chatState.session){chatState.stream=null;try{for(let i=0;i<370;i++){if(view!==chatState.view||sid!==chatState.session)break;const saved=await api(`/api/chat/sessions/${sid}/messages/${rid}`);mergeRecord(saved);if(saved.status!=='pending')break;await new Promise(resolve=>setTimeout(resolve,500));}}catch(e){$('chat-error').textContent='저장 상태를 확인할 수 없습니다. 새로고침 후 확인하세요. 자동 재생성하지 않습니다.';}refreshSessionList();}}
}
async function sendQuestion(existing=null){
 if(chatState.pending||chatState.accepting||chatState.loading)return;const sid=chatState.session;if(!sid){$('chat-error').textContent='대화를 먼저 시작하세요.';return;}const question=existing?.question??$('chat-question').value;if(!question.trim()){$('chat-error').textContent='공백이 아닌 질문을 입력하세요.';return;}
 const box=existing||{question,request_id:crypto.randomUUID().replaceAll('-','')};const unresolved=outbox(sid);if(!existing&&unresolved){$('chat-error').textContent='앞선 전송의 저장 여부가 불확실합니다. 전송 확인·복구를 먼저 사용하세요.';return;}
 const view=chatState.view;const revision=chatState.draftRevision;persist(outboxKey(sid),JSON.stringify(box));chatState.accepting=true;chatBusy();$('chat-error').textContent='';
 try{const accepted=await api('/api/chat/sessions/'+sid+'/messages',box);if(!accepted.accepted)throw Error('저장 수락을 확인하지 못했습니다.');persist(outboxKey(sid),null);if(view!==chatState.view||sid!==chatState.session)return;
  // Only the submitted draft is cleared, exactly on acceptance. Completion never clears input.
  if(chatState.draftRevision===revision&&$('chat-question').value===question){$('chat-question').value='';saveDraft();}
  mergeRecord(accepted.record);if(accepted.record.status==='pending')watch(sid,accepted.record.request_id,view);refreshSessionList();
 }catch(e){if(view===chatState.view){$('chat-error').textContent=e.message+' · 원문과 요청 ID를 보존했습니다. 전송 확인·복구는 같은 ID를 사용합니다.';}}
 finally{if(view===chatState.view){chatState.accepting=false;chatBusy();}}
}
$('chat-form').onsubmit=e=>{e.preventDefault();sendQuestion();};
$('chat-question').addEventListener('compositionstart',()=>{chatState.composing=true;});$('chat-question').addEventListener('compositionend',()=>{chatState.composing=false;chatState.compositionEnd=performance.now();});
$('chat-question').onkeydown=e=>{if(e.key!=='Enter'||e.shiftKey)return;if(e.isComposing||chatState.composing||e.keyCode===229||performance.now()-chatState.compositionEnd<50)return;e.preventDefault();$('chat-form').requestSubmit();};
$('chat-new').onclick=()=>loadChat(true);$('chat-sessions').onchange=e=>loadChat(false,e.target.value);$('chat-more').onclick=()=>refreshSessionList(true);$('chat-retry').onclick=()=>{const b=outbox(chatState.session);if(b)sendQuestion(b);};
$('chat-cancel').onclick=async()=>{const r=chatState.turns.find(x=>x.status==='pending');if(!r)return;try{await api(`/api/chat/sessions/${r.session_id}/messages/${r.request_id}/cancel`,{});chatState.stream?.abort();for(let i=0;i<40;i++){const state=await api(`/api/chat/sessions/${r.session_id}/messages/${r.request_id}`);if(state.session_id!==chatState.session)break;mergeRecord(state);if(state.status!=='pending')break;await new Promise(resolve=>setTimeout(resolve,500));}}catch(e){$('chat-error').textContent=e.message;}};
