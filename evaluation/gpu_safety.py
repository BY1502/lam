"""Fail-closed admission checks. Starting the next engine requires an empty GPU."""
import time
IDLE_MEMORY_LIMIT_MIB=64

def released(snapshot):
 return (not snapshot['processes'] and bool(snapshot['gpus']) and
         all(g['memory_used_mib']<=IDLE_MEMORY_LIMIT_MIB for g in snapshot['gpus']))

def wait_released(read_state,timeout=60,interval=.5):
 deadline=time.monotonic()+timeout;consecutive=0
 while time.monotonic()<deadline:
  snapshot=read_state();consecutive=consecutive+1 if released(snapshot) else 0
  if consecutive>=2:return snapshot
  time.sleep(interval)
 raise RuntimeError('GPU 프로세스와 잔류 메모리 해제를 확인하지 못해 다음 모델 적재를 차단했습니다.')

def exclusive(backend,snapshot):
 models=snapshot['ollama_ps']['models']
 if backend=='vllm_qwen':
  if snapshot['ollama_running'] or models:raise RuntimeError('vLLM 실행 중에는 Ollama 컨테이너가 중지되어야 합니다.')
  if not snapshot['vllm_running']:raise RuntimeError('vLLM이 실행 중이 아닙니다.')
 else:
  if snapshot['vllm_running']:raise RuntimeError('Ollama 실행 중에는 vLLM 컨테이너가 중지되어야 합니다.')
  if not snapshot['ollama_running']:raise RuntimeError('Ollama가 실행 중이 아닙니다.')
  expected='qwen3.8:27b' if backend=='ollama_qwen' else 'gpt-oss:120b'
  if len(models)>1 or any(m['name']!=expected for m in models):raise RuntimeError('허용 모델 외 적재 또는 여러 모델 적재 감지; 평가를 중단합니다.')
