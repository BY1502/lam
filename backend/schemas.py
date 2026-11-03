from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator

class ClassificationRequest(BaseModel):
 model_config=ConfigDict(extra='forbid')
 backend:Literal['vllm_qwen','ollama_qwen','ollama_gptoss']
 question:str=Field(min_length=1,max_length=4000)
 @field_validator('question')
 @classmethod
 def nonempty(cls,v):
  if not v.strip():raise ValueError('질문을 입력하세요.')
  return v
