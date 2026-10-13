from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date
import sqlite3,json,gzip,re
import verify_money as vm
vm.COLS=1;vm.ROWS=60;vm.CW=300;vm.CH=56;vm.SIZE=60
r=vm.ROOT;db=sqlite3.connect(vm.DB,timeout=60)
keys=set()
for p in (r/'ocr/page-cache').glob('*.json.gz'):
 with gzip.open(p,'rt') as f:obj=json.load(f)
 for row in obj['rows']:
  for c,reason in row['issues']:
   if c in (0,5,6,7) and row['cell_keys'][c]:keys.add(row['cell_keys'][c])
print(json.dumps({'stage':'other_fields','unique_cells':len(keys)}),flush=True)
records=[]
for k in sorted(keys):
 rec=db.execute('SELECT key,height,width,gray,text,confidence,vision_text FROM cells WHERE key=?',(k,)).fetchone()
 if rec[6] is None:records.append(rec[:6])
with ThreadPoolExecutor(max_workers=4) as pool:
 fs=[pool.submit(vm.worker,(100000+i,records[i:i+vm.SIZE])) for i in range(0,len(records),vm.SIZE)]
 for f in as_completed(fs):
  updates,_,_=f.result();db.executemany('UPDATE cells SET vision_text=?,vision_confidence=? WHERE key=?',updates);db.commit()
print(json.dumps({'stage':'other_fields_finished','checked':len(records)}),flush=True)
