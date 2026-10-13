from pathlib import Path
from decimal import Decimal
import json,sqlite3,gzip,shutil
r=Path('/Volumes/250G/yooli_archive');ocr=r/'ocr'
items=json.loads((ocr/'money-unresolved.json').read_text())
confirmed='885.11 444.75 971.44 4277.77 444.18 468.22 433.41 3807.44 5702.48 3444.88 1607.44 4380.84 2884.22 485.27 1387.41 5356.44 4884.62 2614.48 4473.72 1197.04 1442.45 2668.4 3890.4 4354.2 362.46 747.44 440.32 3442.56 4026.82 9317.27 447.96 177.60 316.40 502.72 1482.46 997.32 474.85 866.82 875.61 4636.44 1547.44 450.88 1618.9 832.8 4417.2 2201.5 624.2 4448.79 867.16 452.82 634.52 2711.80'.split()
assert len(confirmed)==len(items)==52
reviews=[]
for i,(item,value) in enumerate(zip(items,confirmed),1):
 assert Decimal(item['primary'])==Decimal(value),(i,item,value)
 reviews.append(dict(item,confirmed_text=value,status='原图逐项复核一致',review_sheet=f'money-review-{1+(i-1)//26}.png',review_cell=1+(i-1)%26))
(ocr/'money-source-reviews.json').write_text(json.dumps(reviews,ensure_ascii=False,indent=2))
review_dir=ocr/'review-evidence';review_dir.mkdir(exist_ok=True)
for i in (1,2):shutil.copy2(f'/tmp/yooli-ocr-work/money-review-{i}.png',review_dir/f'money-review-{i}.png')
# Preserve the original derived cache, then remove a positively identified title row.
key='1f8ae6f320f30e9a9a6881ef0935893f2fa3a64620ff938b8748cec9eff8bc06'
p=ocr/'page-cache'/(key+'.json.gz')
with gzip.open(p,'rt') as f:obj=json.load(f)
if obj['rows'][0]['values'][0] is None and '公示' in ''.join(obj['rows'][0]['raw']):
 old=ocr/'initial-derived-cache';old.mkdir(exist_ok=True);shutil.copy2(p,old/p.name)
 excluded=obj['rows'].pop(0);bbox=obj['grid']['rows'].pop(0)
 obj['excluded_title_rows']=[{'bbox':bbox,'raw':excluded['raw'],'reason':'Article title, not borrower data'}]
 with gzip.open(p,'wt',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False)
 invp=ocr/'inventory.json';inv=json.loads(invp.read_text())
 for image in inv['images']:
  if Path(image['path']).stem==key:image['grid']['rows']=obj['grid']['rows']
 inv['summary']['unique_rows']-=1;inv['summary']['row_references']-=1
 invp.write_text(json.dumps(inv,ensure_ascii=False,indent=2))
 print('Excluded one title row from batch 47.')
print('Recorded visually reviewed money cells:',len(reviews))
