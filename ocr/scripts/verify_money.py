from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import sqlite3,cv2,numpy as np,zlib,json,subprocess,time,re
ROOT=Path('/Volumes/250G/yooli_archive');DB=ROOT/'ocr/cells.sqlite'
HELPER='/tmp/yooli-ocr-work/vision_ocr';LOG='/tmp/yooli-ocr-work/full-ocr.log'
TMP=Path('/tmp/yooli-ocr-work/money-mosaics');TMP.mkdir(exist_ok=True)
COLS=10;ROWS=40;CW=190;CH=48;SIZE=COLS*ROWS

def worker(task):
 worker_id,records=task
 canvas=np.full((ROWS*CH,COLS*CW),255,np.uint8)
 for i,(key,h,w,data,text,confidence) in enumerate(records):
  g=np.frombuffer(zlib.decompress(data),np.uint8).reshape(h,w)
  scale=min(31/h,(CW-24)/w)
  g=cv2.resize(g,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
  y=i//COLS*CH+(CH-g.shape[0])//2;x=i%COLS*CW+(CW-g.shape[1])//2
  canvas[y:y+g.shape[0],x:x+g.shape[1]]=g
 image=TMP/f'money-{worker_id}.png';cv2.imwrite(str(image),canvas)
 proc=subprocess.run([HELPER]+globals().get('OCR_ARGS',['--numeric']),input=str(image)+'\n',text=True,capture_output=True,check=True)
 result=json.loads(proc.stdout)
 if result.get('error'):raise RuntimeError(result['error'])
 parts=[[] for _ in records]
 def append(box,text,confidence):
  if not text.strip() or box[2]<=0 or box[3]<=0:return
  x,y,w,h=box;px=(x+w/2)*canvas.shape[1];py=(1-y-h/2)*canvas.shape[0]
  c=int(px//CW);r=int(py//CH);i=r*COLS+c
  if 0<=c<COLS and 0<=r<ROWS and i<len(records):parts[i].append((px,text,float(confidence)))
 for obs in result.get('observations',[]):
  x,y,w,h=obs['box']
  px=(x+w/2)*canvas.shape[1]
  fits=(x*canvas.shape[1]//CW)==((x+w)*canvas.shape[1]//CW) and h*canvas.shape[0]<CH*1.2
  if fits:append(obs['box'],obs['text'],obs['confidence'])
  else:
   for ch in obs.get('chars',[]):append(ch['box'],ch['text'],obs['confidence'])
 updates=[];recognized=0;disagreements=0
 for record,textparts in zip(records,parts):
  textparts.sort(key=lambda x:x[0]);text=''.join(x[1] for x in textparts).strip();confidence=min((x[2] for x in textparts),default=0)
  key=record[0];updates.append((text,confidence,key))
  recognized+=bool(text)
  a=re.sub(r'\s+','',record[4]);b=re.sub(r'\s+','',text)
  from decimal import Decimal,InvalidOperation
  try:same=bool(a and b) and Decimal(a)==Decimal(b)
  except InvalidOperation:same=False
  disagreements+=not same
 return updates,recognized,disagreements

def main():
 db=sqlite3.connect(DB,timeout=60);db.execute('PRAGMA journal_mode=WAL')
 total=0;missing=0;disagree=0;empty=0
 while True:
  records=db.execute('SELECT key,height,width,gray,text,confidence FROM cells WHERE roles&1=1 AND vision_text IS NULL LIMIT ?', (SIZE*2,)).fetchall()
  finished=Path(LOG).exists() and '"stage": "export"' in Path(LOG).read_text()
  if not records:
   if finished:break
   time.sleep(3);continue
  if len(records)<SIZE and not finished:time.sleep(2);continue
  with ThreadPoolExecutor(max_workers=2) as pool:
   fs=[pool.submit(worker,(i,records[i*SIZE:(i+1)*SIZE])) for i in range((len(records)+SIZE-1)//SIZE)]
   for f in as_completed(fs):
    updates,recognized,errors=f.result()
    db.executemany('UPDATE cells SET vision_text=?,vision_confidence=? WHERE key=?',updates);db.commit()
    total+=len(updates);disagree+=errors;empty+=len(updates)-recognized
  state={'stage':'money_check','unique_cells_checked':total,'disagreements':disagree,'not_recognized':empty}
  (ROOT/'ocr/money-progress.json').write_text(json.dumps(state,ensure_ascii=False,indent=2));print(json.dumps(state),flush=True)
 print('Money verification complete.',flush=True)
if __name__=='__main__':main()
