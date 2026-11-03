"""Environment-only configuration; keys never enter saved records."""
from dataclasses import dataclass
from pathlib import Path
import os
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
TAXONOMY_VERSION='food-manufacturing-second-review-draft-v2.0.0'
PROMPT_VERSION='classification-only-second-review-v2.0.0'
TAXONOMY_FILE='taxonomy/intents_v2.json'
PROMPT_FILE='prompts/classification_v2.md'
INTENT_IDS=('status.query','history.query','knowledge.lookup','data.analyze','cause.analyze','case.search','process.predict','condition.recommend','work.plan','whatif.simulate','process.status','proposal.apply','dt.update','clarify','out_of_scope')
SCHEMA={'type':'object','properties':{'intents':{'type':'array','items':{'type':'string','enum':list(INTENT_IDS)},'minItems':1}},'required':['intents'],'additionalProperties':False}
@dataclass(frozen=True)
class Target:
 id:str
 engine:str
 url:str
 model:str
 think:bool|str
 key:str=''

def url(value):
 p=urlsplit(value)
 if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment:raise ValueError('API 주소에는 인증정보/쿼리를 포함할 수 없습니다.')
 return value.rstrip('/')

def targets():
 ollama=url(os.getenv('OLLAMA_URL','http://127.0.0.1:11434'))
 return {
 'vllm_qwen':Target('vllm_qwen','vllm',url(os.getenv('VLLM_URL','http://127.0.0.1:8001/v1')),os.getenv('VLLM_MODEL','qwen3.8:27b'),False,os.getenv('VLLM_API_KEY','')),
 'ollama_qwen':Target('ollama_qwen','ollama',ollama,os.getenv('OLLAMA_QWEN_MODEL','qwen3.8:27b'),False,os.getenv('OLLAMA_API_KEY','')),
 'ollama_gptoss':Target('ollama_gptoss','ollama',ollama,os.getenv('OLLAMA_GPTOSS_MODEL','gpt-oss:120b'),'low',os.getenv('OLLAMA_API_KEY',''))}

def clean(value):
 secrets=[os.getenv(k,'') for k in ['VLLM_API_KEY','OLLAMA_API_KEY'] if os.getenv(k)]
 if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k.lower() not in ('authorization','api_key','key')}
 if isinstance(value,list):return [clean(v) for v in value]
 if isinstance(value,str):
  for s in secrets:value=value.replace(s,'[REDACTED]')
 return value
