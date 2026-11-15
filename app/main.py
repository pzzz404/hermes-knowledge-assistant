"""Single-product source-grounded documentation chat."""
import asyncio,html,json,re,time,hashlib,hmac
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse,HTMLResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from .config import Settings
from .corpus import Corpus
from .hermes import Hermes,MockHermes
from .models import ChatInput,GeneratedAnswer
from .prompt import SYSTEM
from .store import Store,AppError
STATIC=Path(__file__).parent/'static'

def version_of(question,history):
    found=re.findall(r'\bV[12]\b',question,re.I)
    if found:return found[-1].upper()
    if history:
        old=re.findall(r'\bV[12]\b',history[-1]['question'],re.I)
        if old and re.search(r'\b(it|that|there|also|instead)\b',question,re.I):return old[-1].upper()
    return None

def scope_of(question):return 'procedure' if re.search(r'\b(how|steps|procedure|walk me through)\b',question,re.I) else 'overview'

def boundary(question,history,version,evidence):
    if re.search(r'\b(system|developer|hidden)\s+(prompt|instructions)|\b(api key|secret|other users?.*conversations?)\b',question,re.I):
        return GeneratedAnswer(status='not_found',summary='I cannot disclose private instructions, credentials or other conversations.')
    if re.search(r'\b(can|could|will|would) you\s+(?:please\s+)?(invite|export|archive|restore|delete|change|send|open|access|reset)\b',question,re.I) and not re.search(r'\b(how|explain|steps)\b',question,re.I):
        return GeneratedAnswer(status='not_found',summary='I can explain the documentation but cannot perform actions in a workspace.')
    if re.search(r'\b(?:permanent(?:ly)?\s+delet\w*|delet\w*\s+permanent(?:ly)?)\b',question,re.I):
        if any('Permanent deletion is a separate operation and is not described' in e['text'] for e in evidence):
            return GeneratedAnswer(status='not_found',summary='The documentation does not specify permanent deletion or a 30-day deletion rule for archived projects.')
    if re.search(r'\b(it|that|this|those)\b',question,re.I) and not history and len(question.split())<8:
        return GeneratedAnswer(status='needs_clarification',summary='I need to know which setting you mean.',clarifying_question='Which feature or setting are you asking about?')
    if re.search(r'\b(project settings|project navigation|command palette)\b',question,re.I) and not version:
        return GeneratedAnswer(status='needs_clarification',summary='The interface differs by version.',clarifying_question='Are you using interface V1 or V2?')
    if not evidence:return GeneratedAnswer(status='not_found',summary='The selected documentation does not establish an answer to that question.')
    return None

def validate(raw,evidence,scope):
    a=GeneratedAnswer.model_validate_json(raw)
    ids={e['evidence_id'] for e in evidence}
    if not set(a.evidence_ids).issubset(ids):raise ValueError('Unknown evidence ID')
    if any(not set(s.evidence_ids).issubset(set(a.evidence_ids)) for s in a.steps):raise ValueError('Step evidence mismatch')
    prose=' '.join([a.summary,a.clarifying_question or '',*[s.text for s in ([] if scope=='overview' else a.steps)]])
    cited=' '.join(e['text'] for e in evidence if e['evidence_id'] in a.evidence_ids)
    if (re.search(r'\b(?:only\s+(?:a\s+)?(?:workspace\s+)?owner\s+can|owner\s+can)\s+transfer\s+billing\s+ownership\b',prose,re.I)
            and not re.search(r'\bowner\s+can\s+transfer\s+billing\s+ownership\b',cited,re.I)):
        raise ValueError('Source only forbids Admin transfer. Do not infer that an Owner can transfer; state the documented Admin restriction only.')
    if re.search(r'https?://|javascript:|<\s*/?\w+[^>]*>',prose,re.I):raise ValueError('Generated URL or HTML')
    if scope=='overview':a=a.model_copy(update={'steps':[]})
    if a.status!='answered':a=a.model_copy(update={'evidence_ids':[]})
    Corpus.sources(evidence,a.evidence_ids)
    return a

def create_app(settings=None,adapter=None):
    cfg=settings or Settings();store=Store(cfg);corpus=Corpus(cfg.data_dir)
    llm=adapter or (Hermes(cfg) if cfg.adapter=='hermes' else MockHermes())
    @asynccontextmanager
    async def lifespan(app):
        yield
    app=FastAPI(title='Knowledge Support Assistant',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.store=store;app.state.corpus=corpus
    auth=hashlib.sha256(('knowledge-invite:'+cfg.invite_token).encode()).hexdigest()
    @app.middleware('http')
    async def security(request,call_next):
        response=None
        if request.method=='POST':
            origin=request.headers.get('origin');expected=cfg.public_origin.rstrip('/') or str(request.base_url).rstrip('/')
            if origin and origin!=expected:response=JSONResponse({'error':{'code':'origin_rejected','message':'Use this original browser tab.'}},403)
            if int(request.headers.get('content-length','0') or 0)>16000:response=JSONResponse({'error':{'code':'request_too_large','message':'Request too large.'}},413)
        if cfg.invite_token and request.url.path.startswith('/api/') and request.url.path!='/api/access':
            if not hmac.compare_digest(request.cookies.get('knowledge_access',''),auth):response=JSONResponse({'error':{'code':'invite_required','message':'Open your private invitation link.'}},401)
        response=response or await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Cache-Control':'no-store',
           'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"})
        return response
    @app.exception_handler(AppError)
    async def err(request,exc):return JSONResponse({'error':{'code':exc.code,'message':exc.message}},exc.http_status)
    @app.exception_handler(RequestValidationError)
    async def invalid(request,exc):return JSONResponse({'error':{'code':'invalid_request','message':'Enter a valid question and request ID.'}},422)
    def with_cookie(body,row,code=200):
        out=JSONResponse(body,code);out.set_cookie('knowledge_session',row['id'],secure=cfg.cookie_secure,httponly=True,samesite='strict',max_age=cfg.session_ttl_hours*3600);return out
    @app.get('/')
    async def index():return FileResponse(STATIC/'index.html')
    @app.get('/about')
    async def about():return FileResponse(STATIC/'about.html')
    @app.get('/kb/{slug}')
    async def kb(slug:str):
        if not re.fullmatch(r'[a-z0-9-]+',slug):raise AppError('not_found','Article not found.',404)
        doc=corpus.article(slug)
        if not doc:raise AppError('not_found','Article not found.',404)
        body='<main><a href="/">← Chat</a><p>Example Workspace · fictional demo documentation · '+html.escape(doc['version'])+'</p><article><pre style="white-space:pre-wrap;font:inherit">'+html.escape(doc['body'])+'</pre></article></main>'
        return HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(doc['title'])+'</title><link rel="stylesheet" href="/static/style.css">'+body+'</html>')
    @app.get('/health')
    async def health():return {'status':'ok'}
    @app.get('/ready')
    async def ready():
        good=corpus.coverage()['article_count']>0 and cfg.adapter=='hermes' and await llm.ready()
        return JSONResponse({'ready':good,'article_count':corpus.coverage()['article_count'],'mode':cfg.adapter},200 if good else 503)
    @app.post('/api/access')
    async def access(request:Request):
        data=await request.json()
        if not cfg.invite_token or not hmac.compare_digest(str(data.get('token','')),cfg.invite_token):raise AppError('invalid_invite','Invalid invitation.',401)
        out=JSONResponse({'ok':True});out.set_cookie('knowledge_access',auth,secure=cfg.cookie_secure,httponly=True,samesite='strict',max_age=86400);return out
    @app.get('/api/coverage')
    async def coverage():return {**corpus.coverage(),'mode':cfg.adapter}
    @app.get('/api/conversation')
    async def conversation(request:Request):
        row=store.session(request.cookies.get('knowledge_session'));return with_cookie({'history':json.loads(row['history'])},row)
    @app.post('/api/new-conversation')
    async def new_conversation(request:Request):
        row=store.session(request.cookies.get('knowledge_session'),True);return with_cookie({'history':[]},row)
    @app.post('/api/chat')
    async def chat(data:ChatInput,request:Request):
        row=store.session(request.cookies.get('knowledge_session'));rid=str(data.request_id);sid=row['id']
        replay=store.admit(sid,rid,data.question,request.client.host if request.client else 'unknown')
        if replay:return with_cookie(replay[1],row,replay[0])
        start=time.monotonic();meta={'model':cfg.hermes_model};code=200
        try:
            history=json.loads(row['history']);version=version_of(data.question,history);scope=scope_of(data.question)
            evidence=corpus.search(data.question,history,version)
            payload={'question':data.question,'version':version or 'unspecified','scope':scope,
                     'prior_questions_not_evidence':[h['question'] for h in history],
                     'EVIDENCE':[{k:e[k] for k in ('evidence_id','title','heading','version','text')} for e in evidence]}
            answer=boundary(data.question,history,version,evidence)
            async with asyncio.timeout(cfg.timeout_seconds):
                for attempt in range(0 if answer else 2):
                    store.reserve_generation();raw,usage=await llm.complete(SYSTEM,payload,row['hermes_id']);meta['usage']=usage
                    try:answer=validate(raw,evidence,scope);break
                    except (ValueError,ValidationError) as exc:
                        if attempt:raise AppError('invalid_model_response','The answer could not be verified.',502)
                        payload['format_correction']='Correct JSON format and source IDs. '+str(exc)[:300]
            sources=corpus.sources(evidence,answer.evidence_ids)
            result={'request_id':rid,'status':answer.status,'answer':answer.model_dump(exclude={'status','evidence_ids'}),
                    'sources':sources,'corpus_version':corpus.coverage()['corpus_version'],'mode':cfg.adapter}
            meta['evidence_ids']=answer.evidence_ids
        except TimeoutError:code=504;result={'request_id':rid,'error':{'code':'upstream_timeout','message':'The answer took too long.'}}
        except AppError as exc:code=exc.http_status;result={'request_id':rid,'error':{'code':exc.code,'message':exc.message}}
        except Exception:code=503;result={'request_id':rid,'error':{'code':'internal_error','message':'The assistant is temporarily unavailable.'}}
        meta['latency']=round(time.monotonic()-start,3);store.finish(sid,rid,code,result,data.question,meta)
        return with_cookie(result,row,code)
    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    return app

app=create_app()
