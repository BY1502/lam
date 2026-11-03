"""Two-stage chat only. Legacy classification and recorded v2 contracts are unchanged."""
from . import chat_schema as legacy
import copy
VERSION='chat-stream-contract-v3.0.1'
PROMPT_VERSION='chat-analysis-v3.0.1'
REPLY_VERSION='chat-response-v3.0.0'
MEMORY_VERSION='user-context-slots-v1.0.1'
ANALYSIS_TOKENS=4096
REPLY_TOKENS=1536
EVIDENCE=copy.deepcopy(legacy.REQUEST['properties']['evidence'])
REQUEST=copy.deepcopy(legacy.REQUEST)
REQUEST['properties'].update(missing_information=legacy.STRINGS,clarification_question={'type':['string','null'],'minLength':1})
REQUEST['required']+=['missing_information','clarification_question']
SLOT={'type':'string','pattern':'^[a-z][a-z0-9_.]{0,63}$'}
UPDATE={'type':'object','properties':{'slot':SLOT,'value':legacy.STR,'evidence':EVIDENCE},'required':['slot','value','evidence'],'additionalProperties':False}
REMOVE={'type':'object','properties':{'slot':SLOT,'evidence':EVIDENCE},'required':['slot','evidence'],'additionalProperties':False}
MEMORY={'type':'object','properties':{'relation':{'type':'string','enum':['new_task','continue','correct','withdraw']},'updates':{'type':'array','items':UPDATE},'remove':{'type':'array','items':REMOVE}},'required':['relation','updates','remove'],'additionalProperties':False}
SCHEMA={'type':'object','properties':{'requests':{'type':'array','items':REQUEST,'minItems':1},'memory':MEMORY},'required':['requests','memory'],'additionalProperties':False}
