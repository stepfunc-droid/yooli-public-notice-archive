#!/usr/bin/env python3
"""Archive public Youli WeChat notices through their recommendation links."""
import concurrent.futures as futures
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit, parse_qs
import gzip, hashlib, json, re, shutil, struct, time

ROOT=Path('/Volumes/250G/yooli_archive')
FIRST,LAST=295,200
WORKERS=4
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
DIGITS={'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}

def batch_number(title):
    m=re.search(r'第([0-9零〇一二两三四五六七八九十百千]+)批',title)
    if not m: return None
    s=m.group(1)
    if s.isdigit(): return int(s)
    n=0; v=0
    for c in s:
        if c in DIGITS: v=DIGITS[c]
        else: n+=(v or 1)*{'十':10,'百':100,'千':1000}[c]; v=0
    return n+v

def is_notice(title):
    return all(s in title for s in ('有利网','严重失信','借款人','公示')) and batch_number(title) is not None

def url_key(url):
    p=urlsplit(unescape(url)); q=parse_qs(p.query)
    if '__biz' in q and 'mid' in q:
        return (p.netloc,q['__biz'][0],q['mid'][0],q.get('idx',['1'])[0])
    return (p.netloc,p.path,p.query)

def sha(data): return hashlib.sha256(data).hexdigest()
def write_json(path,data):
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.replace(path)
def http_get(url):
    last=None
    for attempt in range(3):
        try:
            req=Request(url,headers={'User-Agent':UA,'Accept-Encoding':'gzip','Referer':'https://mp.weixin.qq.com/'})
            with urlopen(req,timeout=30) as response:
                data=response.read()
                if response.headers.get('Content-Encoding')=='gzip': data=gzip.decompress(data)
                return data,response.url,response.headers.get('Content-Type','')
        except Exception as e:
            last=e
            if attempt<2: time.sleep(1+attempt*2)
    raise RuntimeError(str(last))

class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth=0; self.meta={}; self.tokens=[]; self.text=[]; self.images=[]; self.links=[]; self.anchor=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta' and a.get('property','').startswith('og:'): self.meta[a['property']]=a.get('content','')
        if not self.depth:
            if a.get('id')!='js_content': return
            self.depth=1; a['style']=''; self.tokens.append(('start',tag,a)); return
        self.tokens.append(('start',tag,a))
        if tag not in VOID: self.depth+=1
        if tag=='a' and a.get('href'): self.anchor={'url':a['href'],'text':[]}
        if tag=='img':
            url=a.get('data-src') or a.get('src')
            if url:
                self.images.append({'index':len(self.images)+1,'source_url':url,'declared_width':a.get('data-w',''),'declared_type':a.get('data-type','')})
                a['_archive_image_index']=str(len(self.images))
                self.text.append(f'\n[图片 {len(self.images)}]\n')
        if tag=='br': self.text.append('\n')
    def handle_endtag(self,tag):
        if not self.depth: return
        self.tokens.append(('end',tag,None))
        if tag=='a' and self.anchor:
            self.links.append({'url':self.anchor['url'],'title':''.join(self.anchor['text']).strip()}); self.anchor=None
        if tag not in VOID: self.depth-=1
        if tag in ('p','div','section','h1','h2','h3','tr'): self.text.append('\n')
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID: self.handle_endtag(tag)
    def handle_data(self,data):
        if self.depth:
            self.tokens.append(('text',None,data)); self.text.append(data)
            if self.anchor: self.anchor['text'].append(data)

articles={}
article_errors=[]

def parse_article(data,requested_url):
    text=data.decode('utf-8')
    p=ArticleParser(); p.feed(text)
    title=p.meta.get('og:title','')
    if not is_notice(title) or not p.tokens: raise ValueError('Response is not a complete Youli notice article: '+title[:100])
    batch=batch_number(title)
    m=re.search(r'var create_time\s*=\s*[\"\x27](\d+)',text)
    published=datetime.fromtimestamp(int(m.group(1)),ZoneInfo('Asia/Shanghai')).isoformat() if m else None
    return {'batch':batch,'title':title,'published_at':published,'source_url':p.meta.get('og:url') or requested_url,'requested_url':requested_url,'images':p.images,'recommendations':p.links,'raw_html_sha256':sha(data),'raw_html_bytes':len(data),'fetched_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'status':'html_saved','_parser':p}

def save_article(data,url):
    a=parse_article(data,url)
    b=a['batch']
    if not LAST<=b<=FIRST: return a
    folder=ROOT/f'batch-{b:04d}'; folder.mkdir(exist_ok=True)
    raw=folder/'raw.html'
    if raw.exists() and sha(raw.read_bytes())!=sha(data):
        # Keep an earlier capture instead of silently replacing it.
        data=raw.read_bytes(); a=parse_article(data,url)
    else: raw.write_bytes(data)
    a['folder']=folder.name
    body=re.sub(r'\n[ \t\u200b\xa0]*\n+', '\n\n',''.join(a['_parser'].text)).strip()
    (folder/'body.txt').write_text(body+'\n',encoding='utf-8')
    write_json(folder/'metadata.json',{k:v for k,v in a.items() if not k.startswith('_')})
    return a

def fetch_article(item):
    expected,url=item
    path=ROOT/f'batch-{expected:04d}'/'raw.html'
    if path.exists(): data=path.read_bytes()
    else: data,final,mime=http_get(url)
    a=save_article(data,url)
    if a['batch']!=expected: raise ValueError(f'Expected batch {expected}, got {a["batch"]}')
    return a

def summary_rows():
    return [{k:a.get(k) for k in ('batch','published_at','title','source_url','folder','status')} | {'image_count':len(a['images']),'images_saved':sum(x.get('status')=='saved' for x in a['images']),'image_failures':sum(x.get('status')=='failed' for x in a['images'])} for b,a in sorted(articles.items(),reverse=True)]

def write_lists():
    rows=summary_rows()
    missing=[b for b in range(FIRST,LAST-1,-1) if b not in articles]
    write_json(ROOT/'list.json',{'range':{'from':FIRST,'through':LAST,'inclusive':True},'article_count':len(rows),'missing_batches':missing,'articles':rows,'errors':article_errors})
    (ROOT/'urls.txt').write_text(''.join(r['source_url']+'\n' for r in rows),encoding='utf-8')
    lines=['# 有利网严重失信借款人公示抓取清单','',f'范围：第 {FIRST} 批至第 {LAST} 批（含两端）。已抓取：{len(rows)} 批。','', '| 批次 | 发布日期 | 标题与原链接 | 正文图片 | 状态 | 离线页面 |','|---|---|---|---:|---|---|']
    for r in rows:
        date=(r['published_at'] or '')[:10]
        lines.append(f'| {r["batch"]} | {date} | [{r["title"]}]({r["source_url"]}) | {r["images_saved"]}/{r["image_count"]} | {r["status"]} | [{r["folder"]}]({r["folder"]}/index.html) |')
    if missing: lines.extend(['', '缺少批次：'+', '.join(map(str,missing))])
    (ROOT/'list.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    links='\n'.join(f'<tr><td>{r["batch"]}</td><td>{escape((r["published_at"] or "")[:10])}</td><td><a href="{r["folder"]}/index.html">{escape(r["title"])}</a></td><td>{r["images_saved"]}/{r["image_count"]}</td><td>{escape(r["status"])}</td><td><a href="{escape(r["source_url"],quote=True)}">原文</a></td></tr>' for r in rows)
    (ROOT/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>有利网公示档案</title><style>body{font-family:system-ui;margin:32px}td,th{padding:9px;border:1px solid #ddd}table{border-collapse:collapse}a{color:#1457a6}</style><h1>有利网严重失信借款人公示</h1><p>第295批至第200批，含两端。'+f'已抓取 {len(rows)} 批。</p><table><tr><th>批次</th><th>日期</th><th>离线文章</th><th>图片</th><th>状态</th><th>来源</th></tr>'+links+'</table></html>',encoding='utf-8')

# Stage 1: follow only recommendations whose labels identify in-range notices.
def discover():
    for b,url in [(295,'https://mp.weixin.qq.com/s/krze4LPlbprcFpjEZZceLQ'),(294,'https://mp.weixin.qq.com/s/DQC51QbM6fa1oDyMY2vtaA')]:
        source=Path(f'/tmp/youli-notices-test/{b}.html')
        if source.exists(): articles[b]=save_article(source.read_bytes(),url)
        else: articles[b]=fetch_article((b,url))
    pending={}; attempted=set(articles)
    while True:
        for a in list(articles.values()):
            for link in a['recommendations']:
                if not is_notice(link['title']): continue
                b=batch_number(link['title'])
                if LAST<=b<=FIRST and b not in attempted and b not in articles:
                    if urlsplit(link['url']).hostname=='mp.weixin.qq.com': pending.setdefault(b,link['url'])
        if not pending: break
        wave=sorted(pending.items(),reverse=True)[:WORKERS]
        for b,_ in wave: pending.pop(b); attempted.add(b)
        with futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            tasks={pool.submit(fetch_article,item):item for item in wave}
            for task in futures.as_completed(tasks):
                expected,url=tasks[task]
                try: articles[expected]=task.result()
                except Exception as e:
                    article_errors.append({'batch':expected,'url':url,'error':str(e)}); print('ARTICLE_ERROR',expected,str(e),flush=True)
        write_lists()
        print(json.dumps({'stage':'articles','saved':len(articles),'oldest':min(articles),'pending':len(pending)}),flush=True)
        time.sleep(0.25)

# Original-size CDN variant; fall back to the exact embedded resource URL.
def image_urls(source):
    p=urlsplit(source)
    original=urlunsplit((p.scheme,p.netloc,re.sub(r'/\d+$','/0',p.path),p.query,p.fragment))
    return list(dict.fromkeys([original,source]))

def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'png',struct.unpack('>II',data[16:24])
    if data[:6] in (b'GIF87a',b'GIF89a'): return 'gif',struct.unpack('<HH',data[6:10])
    if data.startswith(b'\xff\xd8'): return 'jpg',None
    if data[:4]==b'RIFF' and data[8:12]==b'WEBP': return 'webp',None
    raise ValueError('Downloaded resource is not a recognized image')

def fetch_image(source):
    key=sha(source.encode())
    manifest=ROOT/'assets'/(key+'.json')
    if manifest.exists():
        m=json.loads(manifest.read_text())
        p=ROOT/m['path']
        if p.exists() and sha(p.read_bytes())==m['sha256']: return m
    errors=[]
    for url in image_urls(source):
        try:
            data,final,mime=http_get(url)
            ext,size=image_type(data)
            name=key+'.'+ext
            target=ROOT/'assets'/name
            tmp=target.with_name(name+'.tmp'); tmp.write_bytes(data); tmp.replace(target)
            m={'source_url':source,'download_url':url,'final_url':final,'path':'assets/'+name,'bytes':len(data),'sha256':sha(data),'format':ext,'dimensions':list(size) if size else None,'status':'saved'}
            write_json(manifest,m)
            return m
        except Exception as e: errors.append(str(e))
    raise RuntimeError('; '.join(errors))

def render_article(a):
    tokens=a['_parser'].tokens; out=[]
    imgs={x['index']:x for x in a['images']}
    for kind,tag,value in tokens:
        if kind=='text': out.append(escape(value)); continue
        if kind=='end': out.append(f'</{tag}>'); continue
        attrs=dict(value)
        ix=attrs.pop('_archive_image_index',None)
        if ix:
            img=imgs[int(ix)]
            attrs={k:v for k,v in attrs.items() if k not in ('src','srcset','data-src','data-srcset','loading')}
            attrs['src']='../'+img['path'] if img.get('status')=='saved' else img['source_url']
            attrs['loading']='lazy'
        if tag=='a' and attrs.get('href'):
            match=next((l for l in a['recommendations'] if l['url']==attrs['href']),None)
            if match and is_notice(match['title']):
                b=batch_number(match['title'])
                if b in articles: attrs['href']=f'../batch-{b:04d}/index.html'
        out.append('<'+tag+''.join(' '+k if v is None else ' '+k+'="'+escape(str(v),quote=True)+'"' for k,v in attrs.items())+'>')
    body=''.join(out)
    heading=f'<header><a href="../index.html">返回批次清单</a><h1>{escape(a["title"])}</h1><p>{escape(a["published_at"] or "")} · <a href="{escape(a["source_url"],quote=True)}">微信原文</a></p></header>'
    style='body{max-width:1080px;margin:32px auto;padding:0 20px;font-family:system-ui;line-height:1.7}img{max-width:100%!important;height:auto!important}#js_content{visibility:visible!important;opacity:1!important}header{border-bottom:1px solid #ddd;margin-bottom:24px}a{color:#1457a6}'
    html='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(a['title'])+'</title><style>'+style+'</style></head><body>'+heading+body+'</body></html>'
    (ROOT/a['folder']/'body.html').write_text(body,encoding='utf-8')
    (ROOT/a['folder']/'index.html').write_text(html,encoding='utf-8')
    a['status']='complete' if all(x.get('status')=='saved' for x in a['images']) else 'image_errors'
    write_json(ROOT/a['folder']/'metadata.json',{k:v for k,v in a.items() if not k.startswith('_')})

def download_images():
    mapping={}
    for a in articles.values():
        for img in a['images']: mapping.setdefault(img['source_url'],[]).append(img)
    done=0
    with futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        tasks={pool.submit(fetch_image,url):url for url in mapping}
        for task in futures.as_completed(tasks):
            url=tasks[task]
            try:
                m=task.result()
                for img in mapping[url]: img.update(m)
            except Exception as e:
                for img in mapping[url]: img.update({'status':'failed','error':str(e)})
                print('IMAGE_ERROR',url,str(e),flush=True)
            done+=1
            if done%40==0 or done==len(mapping):
                write_lists()
                print(json.dumps({'stage':'images','done':done,'total':len(mapping),'article_image_references':sum(len(a['images']) for a in articles.values())}),flush=True)
    for a in articles.values(): render_article(a)
    write_lists()

def main():
    ROOT.mkdir(parents=True,exist_ok=True); (ROOT/'assets').mkdir(exist_ok=True)
    shutil.copy2(__file__,ROOT/'archive_crawler.py')
    discover()
    print(json.dumps({'stage':'discovery_complete','articles':len(articles),'missing':[b for b in range(FIRST,LAST-1,-1) if b not in articles]}),flush=True)
    download_images()
    report={'expected_articles':FIRST-LAST+1,'saved_articles':len(articles),'complete_articles':sum(a['status']=='complete' for a in articles.values()),'missing_batches':[b for b in range(FIRST,LAST-1,-1) if b not in articles],'image_references':sum(len(a['images']) for a in articles.values()),'image_failures':sum(img.get('status')!='saved' for a in articles.values() for img in a['images']),'finished_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()}
    write_json(ROOT/'verification.json',report)
    (ROOT/'README.md').write_text('''# 有利网公示档案\n\n范围：第295批至第200批，包含两端。只沿正文“推荐阅读”中标记为有利网严重失信借款人公示的链接抓取。\n\n- `index.html`：离线浏览总入口。\n- `list.md` / `list.json` / `urls.txt`：批次、发布日期、标题、原文链接、图片和抓取状态清单。\n- `batch-XXXX/raw.html`：完整 HTTP 响应 HTML，保留原样。\n- `batch-XXXX/index.html`：离线正文，包含全部正文图片，已抓取批次的推荐链接指向本地。\n- `batch-XXXX/body.txt`：正文文本；图片以顺序编号占位。\n- `batch-XXXX/metadata.json`：来源、抓取时间、推荐链接、图片来源、本地路径和 SHA-256。\n- `assets/`：正文原始图片字节；优先下载 CDN `/0` 尺寸版本，失败则回退正文嵌入地址；每份图片均记录实际下载地址。\n- `verification.json`：完整性结果。\n- `archive_crawler.py`：可重复运行的抓取脚本，复用已有 HTML 和经过哈希校验的图片。\n\n正文名单是图片，当前保存原始内容，尚未 OCR 或生成借款人统计表。保留原文的姓名、身份证、手机号掩码。动态评论、点赞等需要运行时接口的数据不属于本次静态正文归档；外部跳转目标（如 APP 下载页）仅保留链接。\n''',encoding='utf-8')
    print(json.dumps({'stage':'finished',**report},ensure_ascii=False),flush=True)
    if report['missing_batches'] or report['image_failures']: raise SystemExit(2)

if __name__=='__main__': main()
