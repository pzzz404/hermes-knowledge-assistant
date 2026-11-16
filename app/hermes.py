import json
import httpx
from .store import AppError

class Hermes:
    def __init__(self, settings):
        self.settings=settings

    async def ready(self):
        if not self.settings.hermes_api_key:
            return False
        try:
            async with httpx.AsyncClient(trust_env=False,timeout=3) as client:
                r=await client.get(self.settings.hermes_url+'/v1/capabilities',headers=self.headers)
                return r.status_code==200 and r.json().get('features',{}).get('chat_completions') is True
        except (httpx.HTTPError,ValueError):
            return False

    @property
    def headers(self):
        return {'Authorization':'Bearer '+self.settings.hermes_api_key}

    async def complete(self, system, payload, session_key):
        # SSE is internal only: closing it on timeout invokes the upstream
        # adapter's agent.interrupt(). A failed/unknown call is never retried.
        headers={**self.headers,'X-Hermes-Session-Key':session_key}
        body={'model':'hermes-agent','stream':True,'messages':[
            {'role':'system','content':system},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]}
        content=[]; usage={}; stopped=False
        try:
            async with httpx.AsyncClient(trust_env=False,timeout=self.settings.timeout_seconds) as client:
                async with client.stream('POST',self.settings.hermes_url+'/v1/chat/completions',headers=headers,json=body) as r:
                    if r.status_code!=200:
                        raise AppError('upstream_unavailable','Сервис ответов недоступен. Повторите попытку позже.',502)
                    async for line in r.aiter_lines():
                        if not line.startswith('data: '):
                            continue
                        if line[6:]=='[DONE]':
                            break
                        event=json.loads(line[6:])
                        if event.get('error') or event.get('hermes',{}).get('failed') or event.get('hermes',{}).get('partial'):
                            raise AppError('upstream_incomplete','Сервис не смог завершить ответ.',502)
                        usage=event.get('usage') or usage
                        for choice in event.get('choices',[]):
                            if choice.get('finish_reason') in ('error','length','tool_calls'):
                                raise AppError('upstream_incomplete','Сервис не смог завершить ответ.',502)
                            stopped=stopped or choice.get('finish_reason')=='stop'
                            delta=choice.get('delta',{})
                            if delta.get('tool_calls'):
                                raise AppError('unsafe_capabilities','Нужно проверить конфигурацию сервиса ответов.',503)
                            content.append(delta.get('content') or '')
                            if sum(map(len,content))>24000:
                                raise AppError('upstream_incomplete','Ответ слишком длинный для проверки.',502)
        except (httpx.TimeoutException,TimeoutError):
            raise AppError('upstream_timeout','Сервис слишком долго отвечал. Вопрос сохранён.',504)
        except (httpx.HTTPError,ValueError):
            raise AppError('upstream_unavailable','Сервис ответов недоступен. Повторите попытку позже.',502)
        if not stopped or not ''.join(content).strip():
            raise AppError('upstream_incomplete','Сервис вернул незавершённый ответ.',502)
        return ''.join(content),usage

class MockHermes:
    """Technical preview only; never claims a real product answer."""
    async def ready(self):
        return True
    async def complete(self, system, payload, session_key):
        return json.dumps({'status':'not_found','summary':'MOCK: реальный запрос к Hermes не выполнялся.',
                           'steps':[],'clarifying_question':None,'evidence_ids':[]}),{}
