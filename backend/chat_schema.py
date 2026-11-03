"""Chat-only contract. Existing classification schema/prompt are unchanged."""
from .config import INTENT_IDS
PROMPT_VERSION='chat-intent-explanation-v2.0.0'
SCHEMA_VERSION='chat-intent-schema-v2.0.0'
CONTRACT_VERSION='user-quote-interpretation-v2.0.0'
TAXONOMY_VERSION='food-manufacturing-second-review-draft-ko-v2.0.1'
TAXONOMY_FILE='taxonomy/intents_v2_ko.json'
POLICY_VERSION='chat-partial-request-policy-draft-v1.0.0'
MAX_TOKENS=4096
CONTEXT_TURNS=8
CONTEXT_CHARS=12000
STR={'type':'string','minLength':1}
STRINGS={'type':'array','items':STR}
CONTEXT={'type':'object','properties':{'turn_id':STR,'quote':STR},'required':['turn_id','quote'],'additionalProperties':False}
REQUEST={'type':'object','properties':{'interpreted_request':STR,'intent_id':{'type':'string','enum':list(INTENT_IDS)},'reason':STR,'target_expressions':STRINGS,'time_expression':{'type':['string','null'],'minLength':1},'conditions':STRINGS,'evidence':{'type':'array','items':CONTEXT,'minItems':1}},'required':['interpreted_request','intent_id','reason','target_expressions','time_expression','conditions','evidence'],'additionalProperties':False}
SCHEMA={'type':'object','properties':{'requests':{'type':'array','items':REQUEST,'minItems':1},'reply':STR},'required':['requests','reply'],'additionalProperties':False}
