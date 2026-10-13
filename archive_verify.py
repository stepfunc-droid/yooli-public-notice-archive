from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit,unquote
from hashlib import sha256
from collections import Counter
import json,re
root=Path('/Volumes/250G/yooli_archive')
listing=json.loads((root/'list.json').read_text())
expected=set(range(1,296))
found={x['batch'] for x in listing['articles']}
issues=[]
if found!=expected: issues.append({'type':'batch_coverage','missing':sorted(expected-found),'unexpected':sorted(found-expected)})
class References(HTMLParser):
 def __init__(self): super().__init__(); self.imgs=[]; self.links=[]
 def handle_starttag(self,tag,attrs):
  d=dict(attrs)
  if tag=='img': self.imgs.append(d.get('src',''))
  if tag=='a' and d.get('href'): self.links.append(d['href'])
assets={}; references=0; batches=[]
for row in listing['articles']:
 folder=root/row['folder']; m=json.loads((folder/'metadata.json').read_text())
 raw=(folder/'raw.html').read_bytes()
 if sha256(raw).hexdigest()!=m['raw_html_sha256']: issues.append({'type':'html_hash','batch':m['batch']})
 if m['batch']!=row['batch'] or m['source_url']!=row['source_url'] or m['status']!='complete': issues.append({'type':'metadata','batch':row['batch']})
 if not m.get('published_at'): issues.append({'type':'date_missing','batch':row['batch']})
 p=References(); p.feed((folder/'index.html').read_text())
 if len(p.imgs)!=len(m['images']): issues.append({'type':'offline_image_count','batch':row['batch']})
 for src in p.imgs:
  if urlsplit(src).scheme or not (folder/unquote(src)).is_file(): issues.append({'type':'offline_image_reference','batch':row['batch'],'src':src})
 for href in p.links:
  if not urlsplit(href).scheme and not href.startswith('#') and not (folder/unquote(href.split('#')[0])).is_file(): issues.append({'type':'offline_link','batch':row['batch'],'href':href})
 for image in m['images']:
  references+=1
  if image.get('status')!='saved': issues.append({'type':'image_status','batch':row['batch'],'index':image['index']}); continue
  assets[image['path']]=image
 batches.append({'batch':m['batch'],'date':m['published_at'],'images':len(m['images'])})
for path,m in assets.items():
 p=root/path
 if not p.is_file() or sha256(p.read_bytes()).hexdigest()!=m['sha256']: issues.append({'type':'asset_hash','path':path})
paths=list(root.rglob('*')); files=[p for p in paths if p.is_file()]
report={'expected_articles':295,'saved_articles':len(found),'complete_articles':sum(x['status']=='complete' for x in listing['articles']),'missing_batches':sorted(expected-found),'image_references':references,'unique_images':len(assets),'unique_image_bytes':sum(x['bytes'] for x in assets.values()),'archive_files':len(files),'archive_bytes':sum(p.stat().st_size for p in files),'image_formats':dict(Counter(x['format'] for x in assets.values())),'all_local_references_valid':not any(x['type'].startswith('offline_') for x in issues),'sha256_verified':not any(x['type'] in ('html_hash','asset_hash') for x in issues),'issues':issues}
(root/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
print('First batch:',batches[0],'Last batch:',batches[-1])
if issues: raise SystemExit(2)
