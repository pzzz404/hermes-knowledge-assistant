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
        if old and re.search(r'\b(это|этот|там|также|вместо|it|that|there|also|instead)\b',question,re.I):return old[-1].upper()
    return None

def scope_of(question):return 'procedure' if re.search(r'\b(как|где|порядок|шаги|how|steps|procedure)\b',question,re.I) else 'overview'

def boundary(question,history,version,evidence):
    if re.search(r'(системн|скрыт).{0,25}(промпт|инструкц)|(?:api.?ключ|ключ.?api|секрет|чуж.{0,20}(диалог|переписк)|(?:диалог|переписк).{0,25}(других|чужих)\s+пользоват)|\b(system|developer|hidden)\s+(prompt|instructions)\b',question,re.I):
        return GeneratedAnswer(status='not_found',summary='Я не раскрываю скрытые инструкции, ключи и чужие диалоги.')
    if re.search(r'(можешь|можете|сможешь|сможете).{0,30}(приглас|экспортир|архивир|восстанов|удал|измени|отправ|откр|сброс)|\b(can|could|will|would) you\s+(?:please\s+)?(invite|export|archive|restore|delete|change|send|open|access|reset)\b',question,re.I) and not re.search(r'\b(как|объясни|порядок|шаги|how|explain|steps)\b',question,re.I):
        return GeneratedAnswer(status='not_found',summary='Я могу объяснить инструкцию, но не выполняю действия в рабочем пространстве.')
    if re.search(r'безвозвратн.{0,20}удал|удал.{0,20}(навсегда|безвозвратн)|permanent(?:ly)?\s+delet',question,re.I):
        if any('Безвозвратное удаление — отдельная операция' in e['text'] for e in evidence):
            return GeneratedAnswer(status='not_found',summary='Документация не описывает безвозвратное удаление архивного проекта или правило удаления через 30 дней.')
    if re.search(r'администратор.{0,45}переда\w*.{0,45}(оплат|биллинг)',question,re.I):
        source=next((e for e in evidence if e['article_id']=='billing' and 'не может передавать право управления оплатой' in e['text']),None)
        if source:
            return GeneratedAnswer(status='answered',summary='Администратор не может передавать право управления оплатой. Кто может выполнить такую передачу, статья не устанавливает.',evidence_ids=[source['evidence_id']])
    if re.search(r'\b(это|этот|эти|эту|ту|тот|там|туда|его|её|it|that|this|those)\b',question,re.I) and not history and len(question.split())<8:
        return GeneratedAnswer(status='needs_clarification',summary='Уточните, о какой настройке идёт речь.',clarifying_question='Какую функцию или настройку вы имеете в виду?')
    if re.search(r'(где|как открыть|как найти).{0,35}(настройки проекта|палитр.{0,10}команд)|\b(project settings|project navigation|command palette)\b',question,re.I) and not version:
        return GeneratedAnswer(status='needs_clarification',summary='Расположение зависит от версии интерфейса.',clarifying_question='Вы используете интерфейс V1 или V2?')
    if not evidence:return GeneratedAnswer(status='not_found',summary='В выбранных статьях нет подтверждённого ответа на этот вопрос.')
    return None

def validate(raw,evidence,scope):
    a=GeneratedAnswer.model_validate_json(raw)
    ids={e['evidence_id'] for e in evidence}
    if not set(a.evidence_ids).issubset(ids):raise ValueError('Unknown evidence ID')
    if any(not set(s.evidence_ids).issubset(set(a.evidence_ids)) for s in a.steps):raise ValueError('Step evidence mismatch')
    prose=' '.join([a.summary,a.clarifying_question or '',*[s.text for s in ([] if scope=='overview' else a.steps)]])
    cited=' '.join(e['text'] for e in evidence if e['evidence_id'] in a.evidence_ids)
    if (re.search(r'(владел\w*.{0,45}(переда\w*|смен\w*).{0,45}(оплат|биллинг)|переда\w*.{0,65}только.{0,10}владел\w*|\bowner\s+can\s+transfer\s+billing\s+ownership\b)',prose,re.I)
            and not re.search(r'владелец.{0,45}(может\s+переда\w*|переда\w*).{0,45}(оплат|биллинг)|\bowner\s+can\s+transfer\s+billing\s+ownership\b',cited,re.I)):
        raise ValueError('Источник запрещает передачу Администратору, но не устанавливает право Владельца передавать управление оплатой.')
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
            if origin and origin!=expected:response=JSONResponse({'error':{'code':'origin_rejected','message':'Откройте исходную вкладку чата.'}},403)
            if int(request.headers.get('content-length','0') or 0)>16000:response=JSONResponse({'error':{'code':'request_too_large','message':'Слишком большой запрос.'}},413)
        if cfg.invite_token and request.url.path.startswith('/api/') and request.url.path!='/api/access':
            if not hmac.compare_digest(request.cookies.get('knowledge_access',''),auth):response=JSONResponse({'error':{'code':'invite_required','message':'Откройте персональную ссылку приглашения.'}},401)
        response=response or await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Cache-Control':'no-store',
           'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"})
        return response
    @app.exception_handler(AppError)
    async def err(request,exc):return JSONResponse({'error':{'code':exc.code,'message':exc.message}},exc.http_status)
    @app.exception_handler(RequestValidationError)
    async def invalid(request,exc):return JSONResponse({'error':{'code':'invalid_request','message':'Проверьте вопрос и идентификатор запроса.'}},422)
    def with_cookie(body,row,code=200):
        out=JSONResponse(body,code);out.set_cookie('knowledge_session',row['id'],secure=cfg.cookie_secure,httponly=True,samesite='strict',max_age=cfg.session_ttl_hours*3600);return out
    @app.get('/')
    async def index():return FileResponse(STATIC/'index.html')
    @app.get('/about')
    async def about():return FileResponse(STATIC/'about.html')
    @app.get('/kb/{slug}')
    async def kb(slug:str):
        if not re.fullmatch(r'[a-z0-9-]+',slug):raise AppError('not_found','Статья не найдена.',404)
        doc=corpus.article(slug)
        if not doc:raise AppError('not_found','Статья не найдена.',404)
        body='<main><a href="/">← Чат</a><p>Рабочее пространство «Пример» · образцовая документация · '+html.escape(doc['version'])+'</p><article><pre style="white-space:pre-wrap;font:inherit">'+html.escape(doc['body'])+'</pre></article></main>'
        return HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(doc['title'])+'</title><link rel="stylesheet" href="/static/style.css">'+body+'</html>')
    @app.get('/health')
    async def health():return {'status':'ok'}
    @app.get('/ready')
    async def ready():
        good=corpus.coverage()['article_count']>0 and cfg.adapter=='hermes' and await llm.ready()
        return JSONResponse({'ready':good,'article_count':corpus.coverage()['article_count'],'mode':cfg.adapter},200 if good else 503)
    @app.post('/api/access')
    async def access(request:Request):
        data=await request.json()
        if not cfg.invite_token or not hmac.compare_digest(str(data.get('token','')),cfg.invite_token):raise AppError('invalid_invite','Недействительное приглашение.',401)
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
                        if attempt:raise AppError('invalid_model_response','Не удалось проверить ответ модели по источникам.',502)
                        payload['format_correction']='Correct JSON format and source IDs. '+str(exc)[:300]
            sources=corpus.sources(evidence,answer.evidence_ids)
            result={'request_id':rid,'status':answer.status,'answer':answer.model_dump(exclude={'status','evidence_ids'}),
                    'sources':sources,'corpus_version':corpus.coverage()['corpus_version'],'mode':cfg.adapter}
            meta['evidence_ids']=answer.evidence_ids
        except TimeoutError:code=504;result={'request_id':rid,'error':{'code':'upstream_timeout','message':'Сервис слишком долго готовил ответ.'}}
        except AppError as exc:code=exc.http_status;result={'request_id':rid,'error':{'code':exc.code,'message':exc.message}}
        except Exception:code=503;result={'request_id':rid,'error':{'code':'internal_error','message':'Помощник временно недоступен.'}}
        meta['latency']=round(time.monotonic()-start,3);store.finish(sid,rid,code,result,data.question,meta)
        return with_cookie(result,row,code)
    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    return app

app=create_app()
