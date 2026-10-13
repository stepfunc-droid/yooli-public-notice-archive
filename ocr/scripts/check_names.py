from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import sqlite3,json
import verify_money as vm
vm.COLS=1;vm.ROWS=60;vm.CW=300;vm.CH=56;vm.SIZE=60;vm.OCR_ARGS=[]
root=vm.ROOT;targets=json.loads(Path('/tmp/yooli-ocr-work/name-review-targets.json').read_text())
db=sqlite3.connect(vm.DB,timeout=60);records=[]
for k in targets['keys']:
 r=db.execute('SELECT key,height,width,gray,text,confidence,vision_text FROM cells WHERE key=?',(k,)).fetchone()
 if r[6] is None:records.append(r[:6])
with ThreadPoolExecutor(max_workers=4) as pool:
 fs=[pool.submit(vm.worker,(200000+i,records[i:i+vm.SIZE])) for i in range(0,len(records),vm.SIZE)]
 for f in as_completed(fs):
  updates,_,_=f.result();db.executemany('UPDATE cells SET vision_text=?,vision_confidence=? WHERE key=?',updates);db.commit()
unresolved=[];matched=0
for k in targets['keys']:
 a,b,c=db.execute('SELECT text,vision_text,vision_confidence FROM cells WHERE key=?',(k,)).fetchone()
 if a.strip()==(b or '').strip():matched+=1
 else:unresolved.append({'key':k,'primary':a,'secondary':b,'secondary_confidence':c})
(root/'ocr/name-unresolved.json').write_text(json.dumps(unresolved,ensure_ascii=False,indent=2))
print(json.dumps({'name_cells_checked':len(records),'matched':matched,'unresolved':len(unresolved)}),flush=True)
