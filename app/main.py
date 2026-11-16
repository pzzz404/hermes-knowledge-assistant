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
from .profile import TOPIC_SLUG_HINTS
from .store import Store,AppError
STATIC=Path(__file__).parent/'static'

def scope_of(question):return 'procedure' if re.search(r'\b(как|где|порядок|шаги|how|steps|procedure)\b',question,re.I) else 'overview'

def boundary(question,history,evidence):
    if re.search(r'(системн|скрыт).{0,25}(промпт|инструкц)|(?:api.?ключ|ключ.?api|секрет|чуж.{0,20}(диалог|переписк)|(?:диалог|переписк).{0,25}(других|чужих)\s+пользоват)|\b(system|developer|hidden)\s+(prompt|instructions)\b',question,re.I):
        return GeneratedAnswer(status='not_found',summary='Я не раскрываю скрытые инструкции, ключи и чужие диалоги.')
    personal=re.search(r'\b(мой|моя|мою|моего|мне|у меня|я)\b',question,re.I)
    individual=re.search(r'сколько|какая|какой|какую|размер|остат|остал|статус|одобр|начисл|выплатили|получил',question,re.I)
    if personal and individual and re.search(r'отпуск|зарплат|преми|заявк|выплат',question,re.I):
        return GeneratedAnswer(status='not_found',summary='У меня нет доступа к вашим личным данным и заявкам. Проверьте их в кадровом портале или обратитесь к ответственному специалисту.')
    if re.search(r'\b(оформи|отправь|согласуй|одобри|создай|измени|проверь в системе|посмотри в кабинете)\b|(?:можешь|можете|сможешь|сможете).{0,35}(оформить|отправить|согласовать|одобрить|создать|изменить)',question,re.I) and not re.search(r'\b(как|объясни|порядок|шаги|how|explain|steps)\b',question,re.I):
        return GeneratedAnswer(status='not_found',summary='Я могу объяснить порядок действий по статье, но не оформляю заявки и не выполняю действия за сотрудника.')
    if (re.search(r'\b(это|этот|эти|эту|ту|тот|там|туда|его|её|it|that|this|those)\b',question,re.I)
            and not history and len(question.split())<8
            and not any(term in question.lower() for term in TOPIC_SLUG_HINTS)):
        return GeneratedAnswer(status='needs_clarification',summary='Уточните, о чём идёт речь.',clarifying_question='Какой порядок или правило вас интересует?')
    if not evidence:return GeneratedAnswer(status='not_found',summary='В выбранных статьях нет подтверждённого ответа на этот вопрос.')
    return None

def validate(raw,evidence,scope):
    a=GeneratedAnswer.model_validate_json(raw)
    ids={e['evidence_id'] for e in evidence}
    if not set(a.evidence_ids).issubset(ids):raise ValueError('Unknown evidence ID')
    if any(not set(s.evidence_ids).issubset(set(a.evidence_ids)) for s in a.steps):raise ValueError('Step evidence mismatch')
    prose=' '.join([a.summary,a.clarifying_question or '',*[s.text for s in ([] if scope=='overview' else a.steps)]])
    if any(e['article_id']=='first-day' for e in evidence) and re.search(r'в первый (?:рабочий )?день.{0,70}присыла\w*.{0,30}письм',prose,re.I):
        raise ValueError('Article says HR sends the email before the first day, not during it')
    if any(e['article_id']=='bonuses' for e in evidence) and re.search(r'преми\w*.{0,45}да(ют|ётся|ется)',prose,re.I) and not re.search(r'возможн|не гарант|рассматрива',prose,re.I):
        raise ValueError('Article says a bonus is possible, not guaranteed')
    if re.search(r'https?://|javascript:|<\s*/?\w+[^>]*>',prose,re.I):raise ValueError('Generated URL or HTML')
    if scope=='procedure' and any(e['article_id']=='vacation' for e in evidence) and len(a.steps)>3:
        if len(a.steps)==4 and re.search(r'подтвержд|дожд',a.steps[-1].text,re.I):
            a=a.model_copy(update={'steps':a.steps[:3]})
        else:raise ValueError('Vacation procedure has exactly three steps')
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
        body='<main><a href="/">← Чат</a><p>Внутренняя база знаний вымышленной компании</p><article><pre style="white-space:pre-wrap;font:inherit">'+html.escape(doc['body'])+'</pre></article></main>'
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
            history=json.loads(row['history']);scope=scope_of(data.question)
            evidence=corpus.search(data.question,history)
            payload={'question':data.question,'scope':scope,
                     'prior_questions_not_evidence':[h['question'] for h in history],
                     'EVIDENCE':[{k:e[k] for k in ('evidence_id','title','heading','version','text')} for e in evidence]}
            answer=boundary(data.question,history,evidence)
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
