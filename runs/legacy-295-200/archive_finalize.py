import runpy,json,shutil
from pathlib import Path
ns=runpy.run_path('/tmp/yooli_archive_crawler.py')
root=ns['ROOT']
old=json.loads((root/'list.json').read_text())
history=[]
for e in old.get('errors',[]):
 e=dict(e);e['resolved']=(root/f'batch-{e["batch"]:04d}'/'raw.html').exists();history.append(e)
(root/'crawl-history.json').write_text(json.dumps({'earlier_attempt_errors':history,'resolution':'277 fetched successfully on retry; 253 fetched from the corrected recommendation in batch 254.'},ensure_ascii=False,indent=2)+'\n')
for folder in sorted(root.glob('batch-*')):
 m=json.loads((folder/'metadata.json').read_text())
 p=ns['parse_article']((folder/'raw.html').read_bytes(),m['requested_url'])
 if p['batch']!=m['batch'] or p['raw_html_sha256']!=m['raw_html_sha256']: raise ValueError('Metadata mismatch '+folder.name)
 m['_parser']=p['_parser']; ns['articles'][m['batch']]=m
if set(ns['articles'])!=set(range(200,296)): raise ValueError('Batch range is still incomplete')
print(json.dumps({'stage':'repair','articles':len(ns['articles']),'new_batches':[277,253]}),flush=True)
ns['download_images']()
shutil.copy2(__file__,root/'archive_finalize.py')
shutil.copy2('/tmp/yooli_archive_verify.py',root/'archive_verify.py')
shutil.copy2('/tmp/yooli_archive_crawl.log',root/'crawl.log')
print('All batches rendered; ready for full verification.',flush=True)
