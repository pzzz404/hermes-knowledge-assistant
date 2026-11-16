import hashlib,json,re,sqlite3
from pathlib import Path
from .profile import EXAMPLES,TOPIC_TERMS,TOPIC_SLUG_HINTS

class Corpus:
    def __init__(self,directory:Path):self.path=directory/'corpus.sqlite3'
    def connect(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;return db
    def documents(self):
        if not self.path.exists():return []
        with self.connect() as db:return [json.loads(r['data']) for r in db.execute('SELECT data FROM articles ORDER BY slug')]
    def coverage(self):
        docs=self.documents()
        return {'article_count':len(docs),'corpus_version':hashlib.sha256(''.join(d['content_hash'] for d in docs).encode()).hexdigest(),
                'versions':sorted({d['version'] for d in docs}),'fetched_at':None,
                'topics':[{'title':d['title'],'url':d['url'],'version':d['version']} for d in docs], 'examples':EXAMPLES}
    def article(self,slug):return next((d for d in self.documents() if d['slug']==slug),None)
    def search(self,question,history=(),version=None):
        if not self.path.exists():return []
        if history and re.search(r'\b(это|этот|эти|там|туда|его|её|также|вместо|that|there|also)\b',question,re.I):question=history[-1]['question']+' '+question
        words=re.findall(r'[a-zа-яё0-9]+',question.lower())
        words += [w for word in words for w in TOPIC_TERMS.get(word,'').split()]
        stop={'как','где','когда','какие','какой','можно','может','ли','мне','нам','это','этот','эти','там','для','при','что','если','или','они','его','её','она','все','про','могу','нужно','надо','v1','v2','сотрудник','сотрудника','сотрудников','делать','происходит','есть','уже',
              'how','can','could','what','where','when','does','do','the','and','this','that','workspace','example'}
        words=list(dict.fromkeys(w for w in words if len(w)>2 and w not in stop))[:20]
        if not words:return []
        # Unicode61 does not stem Russian. Prefixes cover common case endings;
        # explicit topic terms bridge different roots (e.g. пригласить / приглашение).
        query=' OR '.join(w[:5]+'*' if len(w)>5 else '"'+w+'"' for w in words)
        with self.connect() as db:
            rows=db.execute('SELECT *, bm25(chunks) rank FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT 18',(query,)).fetchall()
        hinted={slug for term,slug in TOPIC_SLUG_HINTS.items() if term in question.lower()}
        if 'перв' in question.lower() and 'день' in question.lower():hinted.add('first-day')
        if 'рабочий день' in question.lower() and 'first-day' not in hinted:hinted.add('workday')
        rows=sorted(rows,key=lambda row:(row['slug'] not in hinted,row['rank']))
        if version:
            rows=sorted(rows,key=lambda row: (row['version']!=version,row['rank']))
        chosen=[];seen=set()
        for row in rows:
            if row['slug'] in seen or (version and row['version'] not in ('common',version)):continue
            d=self.article(row['slug']);seen.add(row['slug'])
            chosen.append({'evidence_id':row['evidence_id'],'article_id':d['slug'],'title':d['title'],'heading':row['heading'],
                           'version':d['version'],'text':row['text'],'article':d})
            if len(chosen)>=4:break
        return chosen
    @staticmethod
    def sources(evidence,ids):
        by_id={e['evidence_id']:e for e in evidence};sources=[]
        for eid in ids:
            e=by_id[eid];d=e['article']
            if any(s['url']==d['url'] for s in sources):continue
            excerpt_text=re.sub(r'^#\s+[^\n]+\n*','',e['text']).strip()
            excerpt=' '.join(re.split(r'(?<=[.!?])\s+',excerpt_text)[:2])
            sources.append({'title':d['title'],'url':d['url'],'section':e['heading'],'version':d['version'],
                            'excerpt':excerpt[:350].rsplit(' ',1)[0]+'…' if len(excerpt)>350 else excerpt,
                            'evidence_ids':[eid],'content_hash':d['content_hash']})
        return sources
