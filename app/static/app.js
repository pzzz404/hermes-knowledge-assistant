'use strict';
const $ = (s) => document.querySelector(s);
let history = [], busy = false, pending = null, coverage = null;
const text = (tag, value, className) => { const e=document.createElement(tag); e.textContent=value; if(className)e.className=className; return e; };
const versionLabel = value => value==='common'?'':value==='unknown'?'':value;
async function api(path, data) {
  const res=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data),credentials:'same-origin'});
  const body=await res.json();
  if(!res.ok) { const error=new Error(body.error?.message || 'Помощник временно недоступен.'); error.code=body.error?.code; throw error; }
  return body;
}
function setBusy(value) {
  busy=value;
  $('#ask').disabled=value; $('#new-conversation').disabled=value; $('#prepare').disabled=value;
  $('#question').readOnly=value;
  document.querySelectorAll('.example').forEach(e=>e.disabled=value);
  $('#status').textContent=value?'Готовим ответ…':'';
  $('#question-form').setAttribute('aria-busy',String(value));
}
function answerText(item) {
  const a=item.response.answer;
  return [a.summary,...a.steps.map((s,i)=>`${i+1}. ${s.text}`),a.clarifying_question,
    ...item.response.sources.map(s=>`${s.title}\n${s.url}`)].filter(Boolean).join('\n\n');
}
async function copy(value,button,label) {
  try { await navigator.clipboard.writeText(value); button.textContent='Скопировано'; setTimeout(()=>button.textContent=label,1800); }
  catch { $('#error').hidden=false; $('#error').textContent='Не удалось скопировать автоматически. Выделите текст и скопируйте его с клавиатуры.'; }
}
function render(item) {
  const frag=$('#answer-template').content.cloneNode(true);
  const card=frag.querySelector('.exchange'), a=item.response.answer;
  card.querySelector('.question-bubble').textContent=item.question;
  card.querySelector('.summary').textContent=a.summary;
  card.querySelector('.answer-label').textContent=({'answered':'По документации','needs_clarification':'Нужно уточнение','not_found':'Нет сведений в базе'})[item.response.status];
  if(item.response.mode==='mock')card.querySelector('.answer-label').textContent='MOCK · без ответа модели';
  const ol=card.querySelector('.steps');
  a.steps.forEach(step=>ol.append(text('li',step.text)));
  ol.hidden=!a.steps.length;
  card.querySelector('.clarification').textContent=a.clarifying_question || '';
  const sources=card.querySelector('.sources');
  if(item.response.sources.length) sources.append(text('p','Проверьте источники','section-label'));
  item.response.sources.forEach((source,index)=>{
    const details=document.createElement('details');
    const summary=text('summary',`${index+1}. ${source.title}`);
    if(versionLabel(source.version))summary.append(text('small',versionLabel(source.version)));
    details.append(summary,text('p',source.section),text('blockquote',source.excerpt));
    const link=text('a','Открыть статью ↗','source-link');
    try { const url=new URL(source.url,location.origin); if(url.origin!==location.origin || !/^\/kb\/[a-z0-9-]+$/.test(url.pathname))return; link.href=url.pathname; } catch { return; }
    link.target='_blank'; link.rel='noopener noreferrer'; details.append(link); sources.append(details);
  });
  card.querySelector('.copy').addEventListener('click',e=>copy(answerText(item),e.currentTarget,'Скопировать ответ'));
  $('#conversation').append(card); $('#welcome').hidden=true; $('#intro').hidden=true;
}
function showError(error) { $('#error').textContent=error.message; $('#error').hidden=false; }
async function ask(question) {
  if(busy || !question.trim())return;
  $('#error').hidden=true;setBusy(true);
  if(!pending || pending.question!==question)pending={question,request_id:crypto.randomUUID()};
  const request=pending;
  try {
    const result=await api('/api/chat',request);
    const item={question,response:result};history.push(item);history=history.slice(-6);render(item);
    $('#question').value='';$('#counter').textContent='0 / 2 000';pending=null;
  } catch(error) { showError(error); if(error.code && !['request_in_progress','outcome_unknown'].includes(error.code))pending=null; }
  finally { setBusy(false);$('#question').focus(); }
}
$('#question-form').addEventListener('submit',event=>{event.preventDefault();ask($('#question').value.trim());});
$('#question').addEventListener('input',()=>$('#counter').textContent=`${$('#question').value.length.toLocaleString('ru-RU')} / 2 000`);
$('#question').addEventListener('keydown',event=>{if(event.key==='Enter' && !event.shiftKey && !event.isComposing){event.preventDefault();$('#question-form').requestSubmit();}});
$('#new-conversation').addEventListener('click',async()=>{
  if(busy)return;setBusy(true);
  try {await api('/api/new-conversation',{});history=[];pending=null;$('#conversation').replaceChildren();$('#question').value='';$('#counter').textContent='0 / 2 000';$('#welcome').hidden=false;$('#intro').hidden=false;$('#error').hidden=true;}
  catch(e){showError(e);}finally{setBusy(false);$('#question').focus();}
});
$('#prepare').addEventListener('click',()=>{
  const last=history.at(-1), question=$('#question').value.trim() || last?.question || '[Опишите ваш вопрос]';
  $('#support-text').value=`Здравствуйте!\n\nНужна помощь по внутренней базе знаний.\n\nВопрос: ${question}\nКонтекст: [Добавьте детали без персональных данных]\n\nЧто найдено в документации:\n${last?answerText(last):'Ответ по документации пока не получен.'}\n\nЧто осталось неясным:\n${last?.response.answer.clarifying_question || '[Укажите, что нужно уточнить]'}\n\nЭто черновик. Обращение не отправлено.`;
  $('#support-dialog').showModal();
});
$('#copy-support').addEventListener('click',event=>copy($('#support-text').value,event.currentTarget,'Скопировать черновик'));
$('#show-coverage').addEventListener('click',()=>$('#coverage-dialog').showModal());
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>$('#'+button.dataset.close).close()));
let initializing=false;
async function init() {
  if(initializing)return;
  initializing=true;
  try {
    if(location.hash) {
      const token=location.hash.slice(1); window.history.replaceState(null,'',location.pathname);
      await api('/api/access',{token});
    }
    coverage=await api('/api/coverage');
    $('#access').hidden=true;$('#question-form').hidden=false;
    $('#examples').replaceChildren();$('#topics').replaceChildren();
    $('#coverage-summary').textContent=coverage.mode==='mock'?'MOCK · ответы модели отключены':`${coverage.article_count} статей · вопросы сотрудников`;
    $('#coverage-detail').textContent=`Статей: ${coverage.article_count}. ${coverage.fetched_at?'Обновлено '+new Date(coverage.fetched_at).toLocaleDateString('ru-RU')+'.':'Используется локальный корпус.'}`;
    coverage.topics.forEach(topic=>{
      const item=text('li','');const link=text('a',topic.title);
      const url=new URL(topic.url,location.origin);
      if(url.origin===location.origin && /^\/kb\/[a-z0-9-]+$/.test(url.pathname)){
        link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';item.append(link);
        if(versionLabel(topic.version))item.append(text('span',` · ${versionLabel(topic.version)}`));
        $('#topics').append(item);
      }
    });
    const icons=['▤','◷','✦','⌘'];
    const labels=['Оформить отпуск','Рабочий день','Премии и штрафы','Сломался ноутбук'];
    (coverage.examples || []).forEach((question,i)=>{
      const button=text('button','','example');button.type='button';button.append(text('span',icons[i],'example-icon'));
      const label=text('span',labels[i]);label.append(text('small','ВОПРОС СОТРУДНИКА'));button.append(label);
      button.addEventListener('click',()=>{$('#question').value=question;$('#counter').textContent=`${question.length} / 2 000`;$('#question').focus();});
      $('#examples').append(button);
    });
    const state=await api('/api/conversation');history=state.history;$('#conversation').replaceChildren();history.forEach(render);
    $('#welcome').hidden=history.length>0;$('#intro').hidden=history.length>0;
  } catch(error) {
    if(['invite_required','invalid_invite'].includes(error.code)) {$('#access').hidden=false;$('#access-message').textContent=error.message;$('#question-form').hidden=true;$('#welcome').hidden=true;}
    else showError(error);
  } finally {initializing=false;if(location.hash)setTimeout(init,0);}
}
// An invitation opened in a tab already showing the private notice can be
// a same-document fragment navigation. Exchange it without reloading the page.
window.addEventListener('hashchange',()=>{if(location.hash)init();});
init();
