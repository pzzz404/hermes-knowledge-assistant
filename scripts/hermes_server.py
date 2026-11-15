"""Launch the pinned Hermes HTTP adapter in a dedicated profile, loopback only.

Run using the pinned Hermes release's Python. Agent instances are audited before
they can execute. This is the upstream API adapter, not a direct provider client.
"""
import asyncio
import json
import os
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / '.data/hermes'
RELEASE = Path((PROFILE / 'release-path').read_text().strip())
os.umask(0o077)
os.environ['HERMES_HOME'] = str(PROFILE)
os.environ['HERMES_MAX_ITERATIONS'] = '2'
os.environ['HERMES_MAX_TOKENS'] = '1800'
os.environ['API_SERVER_KEY'] = (PROFILE / 'server-key').read_text().strip()
proxy=os.environ.get('HERMES_PROVIDER_PROXY','')
if proxy:
    for name in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
        os.environ[name] = proxy
os.environ['NO_PROXY'] = os.environ['no_proxy'] = 'localhost,127.0.0.1,::1'
sys.path.insert(0, str(RELEASE))
os.chdir(PROFILE)
from dotenv import load_dotenv
load_dotenv(PROFILE / '.env', override=True)
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from aiohttp import web

class AuditedAdapter(APIServerAdapter):
    _generation_slots = threading.BoundedSemaphore(1)
    def _ensure_session_db(self):
        # The application owns bounded history and its retention policy.
        # The dedicated Hermes side is deliberately stateless between turns.
        return None

    def _create_agent(self, *args, **kwargs):
        agent = super()._create_agent(*args, **kwargs)
        agent.max_iterations = 2
        agent._api_max_retries = 1
        agent._persist_session = lambda *a, **k: None
        agent._save_trajectory = lambda *a, **k: None
        agent._session_db = None
        agent.client = agent.client.with_options(max_retries=0)
        original_create = agent.client.chat.completions.create
        calls = 0
        def one_generation(*a, **kw):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RuntimeError('A second provider generation is forbidden in one admitted call.')
            kw['temperature']=0
            return original_create(*a, **kw)
        agent.client.chat.completions.create = one_generation
        original_run = agent.run_conversation
        def bounded_run(*a, **kw):
            if not self._generation_slots.acquire(blocking=False):
                raise RuntimeError('Dedicated Hermes generation capacity is full.')
            try:
                return original_run(*a, **kw)
            finally:
                self._generation_slots.release()
        agent.run_conversation = bounded_run
        tool_names = sorted(agent.valid_tool_names)
        audit = {'tools': tool_names, 'memory_enabled': agent._memory_enabled,
                 'user_profile_enabled': agent._user_profile_enabled,
                 'memory_provider_active': bool(agent._memory_manager),
                 'hermes_home': os.environ['HERMES_HOME'], 'model': agent.model,
                 'max_provider_calls': 1, 'sdk_retries': 0, 'max_iterations': agent.max_iterations,
                 'transcript_persistence': False, 'background_review': False}
        audit['temperature']=0
        if tool_names or agent.tools or agent._memory_enabled or agent._user_profile_enabled or agent._memory_manager:
            raise RuntimeError('Unsafe Hermes capability surface; request refused.')
        # No background self-improvement generation is permitted in this demo.
        agent._spawn_background_review = lambda *a, **k: None
        (PROFILE / 'runtime-audit.json').write_text(json.dumps(audit, indent=2))
        return agent

    async def _write_sse_chat_completion(self, request, completion_id, model, created,
                                         stream_q, agent_task, agent_ref=None,
                                         session_id=None, gateway_session_key=None):
        # This pinned upstream version always emits stop in SSE, even on
        # failure. Validate the core terminal result before emitting any text.
        response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store'})
        await response.prepare(request)
        try:
            while not agent_task.done():
                await asyncio.wait({agent_task},timeout=1)
                if request.transport is None or request.transport.is_closing():
                    raise ConnectionResetError()
                await response.write(b': keepalive\n\n')
                while not stream_q.empty():
                    stream_q.get_nowait()
            result,usage=await agent_task
            diagnostic={k:result.get(k) for k in ['completed','partial','failed','turn_exit_reason','api_calls']}
            (PROFILE/'last-terminal-state.json').write_text(json.dumps(diagnostic))
            ok=(result.get('completed') and not result.get('partial') and not result.get('failed')
                and result.get('turn_exit_reason')=='text_response(finish_reason=stop)' and result.get('final_response'))
            if ok:
                event={'choices':[{'delta':{'content':result['final_response']},'finish_reason':'stop'}],
                       'usage':{'prompt_tokens':usage.get('input_tokens',0),'completion_tokens':usage.get('output_tokens',0),'total_tokens':usage.get('total_tokens',0)}}
            else:
                event={'error':{'code':'agent_incomplete'},'choices':[]}
            await response.write(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode())
        except (ConnectionError, asyncio.CancelledError):
            if agent_ref and agent_ref[0]:
                agent_ref[0].interrupt('Application disconnected or timed out')
            agent_task.cancel()
        except Exception:
            await response.write(b'data: {"error":{"code":"agent_error"}}\n\ndata: [DONE]\n\n')
        return response

async def main():
    adapter = AuditedAdapter(PlatformConfig(enabled=True, extra={'host': '127.0.0.1', 'port': int(os.environ.get('HERMES_PORT','8657'))}))
    if not await adapter.connect():
        raise SystemExit('Dedicated Hermes API could not start.')
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    await adapter.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
