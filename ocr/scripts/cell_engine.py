from pathlib import Path
from collections import Counter
import sqlite3,hashlib,zlib,struct,re,cv2,numpy as np
from rapidocr import RapidOCR

def trim(gray):
 ys,xs=np.where(gray<225)
 if not len(xs):return np.full((4,4),255,np.uint8)
 y0=max(0,int(ys.min())-1);y1=min(gray.shape[0],int(ys.max())+2)
 x0=max(0,int(xs.min())-1);x1=min(gray.shape[1],int(xs.max())+2)
 return np.ascontiguousarray(gray[y0:y1,x0:x1])

def star_split(gray):
 n,labels,stats,centers=cv2.connectedComponentsWithStats(np.uint8(gray<185),8)
 if n<3:return None
 comps=[];maxh=int(stats[1:,3].max())
 for i,(x,y,w,h,area) in enumerate(stats[1:],1):
  if w<3 or h<3 or h>=maxh*.94 or not .55<=w/h<=1.55:continue
  bits=np.uint8(labels[y:y+h,x:x+w]==i)
  key=(int(w),int(h),int(area),bits.tobytes())
  comps.append((int(x),int(y),int(w),int(h),key))
 freq=Counter(c[4] for c in comps)
 if not freq:return None
 key,count=freq.most_common(1)[0]
 if count<2:return None
 stars=sorted([c for c in comps if c[4]==key])
 # A run of identical optical glyphs, not an inferred identifier length.
 spacing=np.diff([x[0] for x in stars])
 if len(spacing) and (spacing.max()>max(3,spacing.min())*1.6):return None
 x0=stars[0][0];x1=stars[-1][0]+stars[-1][2]
 if x0==0:return None
 return {'count':count,'prefix':gray[:,:x0],'suffix':gray[:,x1:],'run':[x0,x1]}

class CellEngine:
 def __init__(self,db_path):
  self.db=sqlite3.connect(str(db_path),timeout=60)
  self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=NORMAL')
  self.db.execute('''CREATE TABLE IF NOT EXISTS cells (key TEXT PRIMARY KEY,height INTEGER,width INTEGER,gray BLOB,text TEXT,confidence REAL,roles INTEGER DEFAULT 0,vision_text TEXT,vision_confidence REAL)''')
  self.db.commit()
  self.engine=RapidOCR(params={'Global.use_det':False,'Global.use_cls':False,'Global.log_level':'warning','EngineConfig.onnxruntime.intra_op_num_threads':1,'EngineConfig.onnxruntime.inter_op_num_threads':1,'Rec.rec_batch_num':128,'Rec.rec_img_shape':[3,48,96]})
  self.inferred=0;self.hits=0
 def recognize(self,crops,roles):
  gs=[trim(x) for x in crops]
  keys=[hashlib.sha256(struct.pack('>II',*g.shape)+g.tobytes()).hexdigest() for g in gs]
  unique={k:g for k,g in zip(keys,gs)}
  found={}
  allkeys=list(unique)
  for start in range(0,len(allkeys),400):
   ks=allkeys[start:start+400]
   for k,t,c in self.db.execute('SELECT key,text,confidence FROM cells WHERE key IN ('+','.join('?' for _ in ks)+')',ks):found[k]=(t,c)
  missing=[k for k in unique if k not in found]
  if missing:
   prepared=[]
   for k in missing:
    g=unique[k]
    scale=max(1.0,32/g.shape[0]);g=cv2.resize(g,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
    g=cv2.copyMakeBorder(g,4,4,4,4,cv2.BORDER_CONSTANT,value=255)
    prepared.append(cv2.cvtColor(g,cv2.COLOR_GRAY2BGR))
   result=self.engine.recognize_txt(prepared)
   records=[]
   for k,txt,score in zip(missing,result.txts,result.scores):
    g=unique[k];records.append((k,*g.shape,zlib.compress(g.tobytes()),txt,float(score)))
   self.db.executemany('INSERT OR IGNORE INTO cells(key,height,width,gray,text,confidence) VALUES(?,?,?,?,?,?)',records)
   self.db.commit()
   for start in range(0,len(missing),400):
    ks=missing[start:start+400]
    for k,t,c in self.db.execute('SELECT key,text,confidence FROM cells WHERE key IN ('+','.join('?' for _ in ks)+')',ks):found[k]=(t,c)
   self.inferred+=len(missing)
  self.hits+=len(keys)-len(missing)
  updates={}
  for k,role in zip(keys,roles):updates[k]=updates.get(k,0)|role
  self.db.executemany('UPDATE cells SET roles=roles|? WHERE key=?',[(v,k) for k,v in updates.items() if v])
  self.db.commit()
  return [(found[k][0],found[k][1],k) for k in keys]
 def page(self,gray,g):
  jobs=[];roles=[];plans=[]
  cmap=g.get('column_map',list(range(len(g['cols']))))
  for y0,y1 in g['rows']:
   row=[None]*8
   for (x0,x1),logical in zip(g['cols'],cmap):
    crop=gray[max(0,y0+1):y1,max(0,x0+1):x1]
    split=star_split(crop) if logical in (1,2,3) else None
    if split:
     pi=len(jobs);jobs.append(split['prefix']);roles.append(0)
     si=None
     if np.any(split['suffix']<225):si=len(jobs);jobs.append(split['suffix']);roles.append(0)
     row[logical]={'prefix':pi,'suffix':si,'stars':split['count'],'optical_mask':True}
    else:
     ji=len(jobs);jobs.append(crop);roles.append(1 if logical==4 else 0)
     row[logical]={'whole':ji,'optical_mask':False}
   plans.append(row)
  recognized=self.recognize(jobs,roles)
  rows=[]
  for plansrow in plans:
   vals=[];conf=[];keys=[];masks=[]
   for plan in plansrow:
    if plan is None:vals.append('');conf.append(1.0);keys.append(None);masks.append(False);continue
    if 'whole' in plan:
     t,c,k=recognized[plan['whole']];vals.append(t);conf.append(c);keys.append(k);masks.append(False)
    else:
     t,c,k=recognized[plan['prefix']]
     if plan['suffix'] is not None:
      st,sc,sk=recognized[plan['suffix']];t=t+'*'*plan['stars']+st;c=min(c,sc)
     else:t=t+'*'*plan['stars']
     vals.append(t);conf.append(c);keys.append(k);masks.append(True)
   rows.append({'raw':vals,'confidence':conf,'cell_keys':keys,'optical_masks':masks})
  return rows
