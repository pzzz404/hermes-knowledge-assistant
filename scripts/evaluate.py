"""Metered real local Hermes evaluation; preserves every original response."""
import hashlib,json,time,uuid,sys,argparse
from datetime import datetime,timezone
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.prompt import VERSION
from app.config import Settings
from app.corpus import Corpus
root=Path(__file__).resolve().parents[1]
questions=root/'evaluation/questions.json';raw=questions.read_bytes()
out=root/'.data/evaluation-first.json'
parser=argparse.ArgumentParser();parser.add_argument('--resume',action='store_true');parser.add_argument('--ids',default='');parser.add_argument('--out',type=Path);args=parser.parse_args()
if args.out:out=args.out
if out.exists() and not args.resume:raise SystemExit('First run already exists; use --resume after an interrupted collection')
report=json.loads(out.read_text()) if args.resume else {'started_at':datetime.now(timezone.utc).isoformat(),'questions_sha256':hashlib.sha256(raw).hexdigest(),
        'prompt_version':VERSION,'model':Settings().hermes_model,'corpus_version':Corpus(Settings().data_dir).coverage()['corpus_version'],
        'code_sha256':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in ('app/main.py','app/corpus.py','app/prompt.py')},'results':[]}
with httpx.Client(base_url='http://127.0.0.1:8087',trust_env=False,timeout=90) as client:
 for case in json.loads(raw):
  if args.ids and case['id'] not in args.ids.split(','):continue
  if case['id'] in {r['case_id'] for r in report['results']}:continue
  with httpx.Client(base_url='http://127.0.0.1:8087',trust_env=False,timeout=90) as session:
   session.get('/api/conversation').raise_for_status()
   turns=[]
   for question in (case['turns'] if 'turns' in case else [case['question']]):
    start=time.monotonic();request_id=str(uuid.uuid4())
    response=session.post('/api/chat',json={'question':question,'request_id':request_id})
    turns.append({'question':question,'request_id':request_id,'http_status':response.status_code,'response':response.json(),'seconds':round(time.monotonic()-start,2)})
    print(case['id'],response.status_code,response.json().get('status',response.json().get('error',{}).get('code')),flush=True)
   report['results'].append({'case_id':case['id'],'kind':case['kind'],'turns':turns})
   out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n')
  time.sleep(2.2)
