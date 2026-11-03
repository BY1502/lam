from copy import deepcopy
from unittest.mock import patch
import pytest
from evaluation.gpu_safety import released,wait_released,exclusive


def empty():return {'processes':[],'gpus':[{'memory_used_mib':2},{'memory_used_mib':2}],'ollama_ps':{'models':[]},'ollama_running':False,'vllm_running':False}

def test_process_or_residual_memory_blocks_next_load():
 s=empty();assert released(s)
 s['processes']=[{'pid':'1'}];assert not released(s)
 s=empty();s['gpus'][1]['memory_used_mib']=40000;assert not released(s)

def test_requires_two_consecutive_empty_snapshots():
 busy=empty();busy['gpus'][0]['memory_used_mib']=1000
 snapshots=[empty(),busy,empty(),empty()];reads=[]
 def read():reads.append(1);return snapshots.pop(0)
 with patch('evaluation.gpu_safety.time.sleep'):wait_released(read,timeout=1)
 assert len(reads)==4

def test_timeout_is_fail_closed():
 busy=empty();busy['processes']=[{'pid':'1'}]
 with pytest.raises(RuntimeError):wait_released(lambda:busy,timeout=.001,interval=0)

def test_vllm_forbids_even_empty_running_ollama_service():
 s=empty();s.update(vllm_running=True,ollama_running=True)
 with pytest.raises(RuntimeError):exclusive('vllm_qwen',s)
 s['ollama_running']=False;exclusive('vllm_qwen',s)

def test_ollama_requires_vllm_stopped_and_at_most_one_correct_model():
 s=empty();s.update(ollama_running=True,vllm_running=True)
 with pytest.raises(RuntimeError):exclusive('ollama_qwen',s)
 s['vllm_running']=False;s['ollama_ps']['models']=[{'name':'qwen3.8:27b'}];exclusive('ollama_qwen',s)
 s['ollama_ps']['models'].append({'name':'gpt-oss:120b'})
 with pytest.raises(RuntimeError):exclusive('ollama_qwen',s)
 s['ollama_ps']['models']=[{'name':'gpt-oss:120b'}]
 with pytest.raises(RuntimeError):exclusive('ollama_qwen',s)
