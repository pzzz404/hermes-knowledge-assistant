"""Import only repository-owned Markdown with validated front matter."""
import hashlib,json,re,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings

def load_docs(folder):
    docs=[]
    for path in sorted(folder.glob('*.md')):
        raw=path.read_text(encoding='utf-8');parts=raw.split('---\n',2)
        if len(parts)!=3 or parts[0]:raise ValueError('Missing front matter: '+path.name)
        meta={}
        for line in parts[1].splitlines():
            key,sep,value=line.partition(':')
            if not sep or key not in ('title','slug','version'):raise ValueError('Invalid metadata')
            meta[key]=value.strip()
        if set(meta)!=set(('title','slug','version')) or not re.fullmatch(r'[a-z0-9-]+',meta['slug']):raise ValueError('Invalid article identity')
        if meta['version'] not in ('common','V1','V2'):raise ValueError('Invalid version')
        if any(d['slug']==meta['slug'] for d in docs):raise ValueError('Duplicate slug')
        body=parts[2].strip();assert body.startswith('# ')
        docs.append({**meta,'body':body,'url':'/kb/'+meta['slug'],
                     'content_hash':hashlib.sha256(raw.encode()).hexdigest(),'file':path.name})
    return docs

def import_docs(settings=None):
    cfg=settings or Settings();docs=load_docs(cfg.corpus_dir)
    cfg.data_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    db=sqlite3.connect(cfg.data_dir/'corpus.sqlite3')
    with db:
        db.executescript('DROP TABLE IF EXISTS articles; DROP TABLE IF EXISTS chunks; CREATE TABLE articles(slug TEXT PRIMARY KEY,data TEXT NOT NULL); CREATE VIRTUAL TABLE chunks USING fts5(evidence_id UNINDEXED,slug UNINDEXED,title,heading,text,version UNINDEXED,tokenize="porter unicode61");')
        for doc in docs:
            db.execute('INSERT INTO articles VALUES(?,?)',(doc['slug'],json.dumps(doc)))
            blocks=re.split(r'(?=^##\s)',doc['body'],flags=re.M)
            for index,block in enumerate(blocks):
                heading=block.splitlines()[0].lstrip('# ').strip()
                db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',(doc['slug']+':'+str(index),doc['slug'],doc['title'],heading,block,doc['version']))
    db.close();(cfg.data_dir/'corpus.sqlite3').chmod(0o600)
    return len(docs)

if __name__=='__main__':print('Imported original fictional articles:',import_docs())
