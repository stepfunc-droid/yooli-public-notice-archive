from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import sqlite3,json,re
from decimal import Decimal,InvalidOperation
import verify_money as vm
vm.COLS=1;vm.ROWS=60;vm.CW=260;vm.CH=56;vm.SIZE=60

def numeric(s):return bool(re.fullmatch(r'\d+(?:\.\d{1,2})?',re.sub(r'\s+','',s or '')))
def equal(a,b):
 try:return bool(b) and Decimal(re.sub(r'\s+','',a))==Decimal(re.sub(r'\s+','',b))
 except InvalidOperation:return False

def main():
 db=sqlite3.connect(vm.DB,timeout=60)
 records=[r[:6] for r in db.execute('SELECT key,height,width,gray,text,confidence,vision_text FROM cells WHERE roles&1=1').fetchall() if numeric(r[4]) and not equal(r[4],r[6])]
 print(json.dumps({'stage':'sparse_recheck','cells':len(records)}),flush=True)
 with ThreadPoolExecutor(max_workers=4) as pool:
  pending={}
  for i in range(0,len(records),vm.SIZE):
   part=records[i:i+vm.SIZE];pending[pool.submit(vm.worker,(100+i,part))]=part
  done=0
  for f in as_completed(pending):
   updates,recognized,errors=f.result()
   db.executemany('UPDATE cells SET vision_text=?,vision_confidence=? WHERE key=?',updates);db.commit();done+=len(updates)
   if done%300<vm.SIZE:print(json.dumps({'done':done,'total':len(records)}),flush=True)
 allrows=db.execute('SELECT key,text,vision_text,confidence,vision_confidence,height,width,gray FROM cells WHERE roles&1=1').fetchall()
 unresolved=[{'key':r[0],'primary':r[1],'secondary':r[2],'primary_confidence':r[3],'secondary_confidence':r[4]} for r in allrows if numeric(r[1]) and not equal(r[1],r[2])]
 target=vm.ROOT/'ocr/money-unresolved.json';target.write_text(json.dumps(unresolved,ensure_ascii=False,indent=2))
 print(json.dumps({'stage':'sparse_finished','unresolved':len(unresolved)}),flush=True)
if __name__=='__main__':main()
