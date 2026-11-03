from pathlib import Path
from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse,StreamingResponse
from fastapi.staticfiles import StaticFiles
from .schemas import ClassificationRequest
from . import classifier,config,llm_client,chatbot
from pydantic import BaseModel,ConfigDict,Field,field_validator
from contextlib import asynccontextmanager
import json,uuid,asyncio
from . import chat_flow

@asynccontextmanager
async def lifespan(app):
 chatbot.recover_interrupted()
 try:yield
 finally:chat_flow.shutdown()

app=FastAPI(title='식품 제조 LAM — 의도 분석·분류',lifespan=lifespan)
class ChatRequest(BaseModel):
 model_config=ConfigDict(extra='forbid')
 request_id:str=Field(pattern='^[0-9a-f]{32}$')
 question:str=Field(min_length=1,max_length=4000)
 @field_validator('question')
 @classmethod
 def not_blank(cls,value):
  if not value.strip():raise ValueError('질문을 입력하세요.')
  return value

@app.post('/api/chat/sessions')
def new_chat():return {**chatbot.create_session(),**chat_flow.versions()}

@app.get('/api/chat/sessions/{session_id}')
def chat_history(session_id:str):
 try:return {'session_id':session_id,'turns':[chat_flow.public(r) for r in chatbot.session_history(session_id)],**chat_flow.versions()}
 except chatbot.SessionMissing:raise HTTPException(404,'대화 세션이 없습니다.')

@app.get('/api/chat/sessions')
def chat_sessions(limit:int=50,offset:int=0):
 if not 1<=limit<=100 or offset<0:raise HTTPException(422,'목록 범위 오류')
 return {'sessions':chat_flow.sessions(limit,offset)}

@app.post('/api/chat/sessions/{session_id}/messages',status_code=202)
def chat_message(session_id:str,request:ChatRequest):
 try:
  r,reused=chat_flow.submit(session_id,request.question,request.request_id)
  return {'accepted':True,'reused':reused,'record':chat_flow.public(r)}
 except chatbot.SessionMissing:raise HTTPException(404,'대화 세션이 없습니다.')
 except (chatbot.SessionBusy,chat_flow.RequestConflict) as exc:raise HTTPException(409,str(exc) or '이 대화의 이전 메시지를 처리 중입니다.')
 except ValueError as exc:raise HTTPException(422,str(exc))

@app.get('/api/chat/sessions/{session_id}/messages/{request_id}')
def chat_record(session_id:str,request_id:str):
 try:return chat_flow.public(chat_flow.get(session_id,request_id))
 except chatbot.SessionMissing:raise HTTPException(404,'해당 세션의 메시지가 없습니다.')

@app.post('/api/chat/sessions/{session_id}/messages/{request_id}/cancel')
def chat_cancel(session_id:str,request_id:str):
 try:return chat_flow.cancel(session_id,request_id)
 except chatbot.SessionMissing:raise HTTPException(404,'해당 세션의 메시지가 없습니다.')

@app.get('/api/chat/sessions/{session_id}/messages/{request_id}/events')
async def chat_events(session_id:str,request_id:str):
 try:chat_flow.get(session_id,request_id)
 except chatbot.SessionMissing:raise HTTPException(404,'해당 세션의 메시지가 없습니다.')
 async def stream():
  iterator=chat_flow.events(session_id,request_id)
  try:
   while True:
    line=await asyncio.to_thread(next,iterator,None)
    if line is None:break
    yield line
  finally:chat_flow.cancel(session_id,request_id,'브라우저 응답 연결 중단')
 return StreamingResponse(stream(),media_type='application/x-ndjson',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

@app.get('/api/chat/sessions/{session_id}/export')
def chat_export(session_id:str):
 try:chatbot.session_history(session_id)
 except chatbot.SessionMissing:raise HTTPException(404,'대화 세션이 없습니다.')
 return StreamingResponse(chat_flow.export(session_id),media_type='application/x-ndjson',headers={'Content-Disposition':'attachment; filename="chat-'+session_id+'.jsonl"','Cache-Control':'no-store'})

@app.get('/api/health')
def health():
 try:classifier.load_taxonomy();taxonomy={'registered':True,**classifier.versions()}
 except Exception:taxonomy={'registered':False,'reason':'분류표가 없거나 형식이 올바르지 않습니다.'}
 try:config.targets();valid=True
 except ValueError:valid=False
 return {'app':'running','taxonomy':taxonomy,'config_valid':valid,'model_connection':'이 응답에서는 확인하지 않음; /api/backends에서 실제 확인'}
@app.get('/api/intents')
def intents():
 try:return {**classifier.versions(),'items':classifier.load_taxonomy()}
 except Exception:raise HTTPException(503,'분류표 미등록 또는 형식 오류')
@app.get('/api/backends')
def backends():
 try:return [llm_client.probe(t) for t in config.targets().values()]
 except ValueError:raise HTTPException(503,'API 주소 설정 오류')
@app.post('/api/classify')
def classify(request:ClassificationRequest):return classifier.classify(request.backend,request.question)
@app.get('/api/evaluations')
def evaluations():
 root=config.ROOT/'evaluation/results';items=[]
 for p in sorted(root.glob('*/manifest.json'),reverse=True):
  manifest=json.loads(p.read_text())
  if manifest.get('versions',{}).get('taxonomy_version')!=config.TAXONOMY_VERSION:continue
  report=p.parent/'summary.json'
  items.append({'id':p.parent.name,'summary':json.loads(report.read_text()) if report.exists() else None,'has_report':(p.parent/'comparison_report.md').exists()})
 return items
@app.get('/api/evaluations/{experiment}/report')
def report(experiment:str):
 if not experiment.replace('-','').replace('_','').isalnum():raise HTTPException(400,'잘못된 실험 ID')
 path=config.ROOT/'evaluation/results'/experiment/'comparison_report.md'
 if not path.is_file():raise HTTPException(404,'보고서 없음')
 return {'text':path.read_text()}
@app.get('/')
def index():return FileResponse(config.ROOT/'frontend/index.html')
app.mount('/static',StaticFiles(directory=config.ROOT/'frontend'),name='static')
