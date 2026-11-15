import hashlib,json,re,sqlite3
from pathlib import Path
from .profile import EXAMPLES,TOPIC_TERMS

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
        if history and re.search(r'\b(it|that|those|them|their|there|also|instead)\b',question,re.I):question=history[-1]['question']+' '+question
        words=re.findall(r'[a-z0-9]+',question.lower())
        words += [w for word in words for w in TOPIC_TERMS.get(word,'').split()]
        stop={'how','can','could','what','where','when','does','do','the','a','an','to','is','are','i','we','you','my','our','for','with','in','on','of','and','from','this','that','it','v1','v2','workspace','example'}
        words=list(dict.fromkeys(w for w in words if len(w)>2 and w not in stop))[:20]
        if not words:return []
        query=' OR '.join('"'+w+'"' for w in words)
        with self.connect() as db:
            rows=db.execute('SELECT *, bm25(chunks) rank FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT 18',(query,)).fetchall()
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
            sources.append({'title':d['title'],'url':d['url'],'section':e['heading'],'version':d['version'],
                            'excerpt':' '.join(e['text'].split()[:25]),'evidence_ids':[eid],'content_hash':d['content_hash']})
        return sources
