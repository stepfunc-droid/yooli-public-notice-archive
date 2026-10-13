from pathlib import Path
import cv2,numpy as np,json,subprocess,re,gzip
from datetime import date

def groups(points):
 if not len(points):return []
 runs=np.split(points,np.where(np.diff(points)>1)[0]+1)
 return [(int(r[0]),int(r[-1]),float(np.mean(r))) for r in runs]

def grid(path,prepare=True,hints=None):
 gray=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
 if gray is None:raise ValueError('Cannot decode image')
 h,w=gray.shape
 binary=np.uint8(gray<185)*255
 horizontal=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,max(35,w//4)),np.uint8))
 vertical=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((max(3,h//5),1),np.uint8))
 hs=groups(np.where((horizontal>0).sum(axis=1)>w*.65)[0])
 vs=groups(np.where((vertical>0).sum(axis=0)>h*.45)[0])
 if hints and h<160 and hints['shape'][1]==w:
  edges=[hints['cols'][0][0]-1]+[b for a,b in hints['cols']]
  vs=[(int(x),int(x),float(x)) for x in edges]
 if len(vs) in (7,8):
  if vs[0][0]>w*.02:vs.insert(0,(-1,-1,-1.0))
  if vs[-1][1]<w*.98:vs.append((w,w,float(w)))
 if len(vs) not in (8,9) or len(hs)<2:return {'kind':'non_table','shape':[h,w],'vertical_lines':len(vs),'horizontal_lines':len(hs)}
 heights=np.diff([r[2] for r in hs])
 median=float(np.median(heights))
 def has_internal(y):
  return sum(binary[max(0,min(h-1,int(y))),max(0,r[0]-1):min(w,r[1]+2)].max()>0 for r in vs[1:-1])>=5
 if hs[0][0]>median*.6 and has_internal(hs[0][0]/2):hs.insert(0,(-1,-1,-1.0))
 if h-1-hs[-1][1]>median*.6 and has_internal((hs[-1][1]+h)/2):hs.append((h,h,float(h)))
 rows=[]
 for lo,hi in zip(hs,hs[1:]):
  height=hi[2]-lo[2]; y=int((hi[2]+lo[2])/2)
  internal=sum(binary[y,max(0,r[0]-1):min(w,r[1]+2)].max()>0 for r in vs[1:-1])
  if median*.60<=height<=median*1.65 and internal>=5:
   rows.append([lo[1]+1,hi[0]])
 if not rows:return {'kind':'non_table','shape':[h,w],'vertical_lines':len(vs),'horizontal_lines':len(hs)}
 cols=[[a[1]+1,b[0]] for a,b in zip(vs,vs[1:])]
 column_map=list(range(8)) if len(cols)==8 else [0,1,2,3,4,5,7]
 # Remove only identified rules, leaving source images unchanged.
 if not prepare:return {'kind':'table','shape':[h,w],'rows':rows,'cols':cols,'column_map':column_map}
 clean=gray.copy()
 for a,b,_ in hs:clean[max(0,a-1):min(h,b+2),:]=255
 for a,b,_ in vs:clean[:,max(0,a-1):min(w,b+2)]=255
 scale=3.0 if w<950 else 1.5
 clean=cv2.resize(clean,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
 return {'kind':'table','shape':[h,w],'rows':rows,'cols':cols,'column_map':column_map,'scale':scale,'clean':clean}

def assign(result,g):
 h,w=g['shape'];nr=len(g['rows'])
 cells=[[[] for _ in range(8)] for _ in range(nr)]
 def locate(box):
  x,y,bw,bh=box; px=(x+bw/2)*w;py=(1-y-bh/2)*h
  row=next((i for i,(a,b) in enumerate(g['rows']) if a-1<=py<=b+1),None)
  col=next((i for i,(a,b) in enumerate(g['cols']) if a-1<=px<=b+1),None)
  return row,col,px,py
 for obs in result.get('observations',[]):
  x,y,bw,bh=obs['box'];row,col,px,py=locate(obs['box'])
  if row is None:continue
  # Vision occasionally merges adjacent columns; character boxes recover boundaries.
  left=x*w;right=(x+bw)*w
  fits=col is not None and left>=g['cols'][col][0]-3 and right<=g['cols'][col][1]+3
  if fits:
   cells[row][col].append((px,py,obs['text'],obs['confidence']))
  else:
   for ch in obs.get('chars',[]):
    ri,ci,cx,cy=locate(ch['box'])
    if ri is not None and ci is not None:cells[ri][ci].append((cx,cy,ch['text'],obs['confidence']))
 output=[]
 for row in cells:
  vals=[];conf=[]
  for parts in row:
   parts.sort(key=lambda p:(round(p[1]/4),p[0]))
   vals.append(''.join(x[2] for x in parts).strip())
   conf.append(min((x[3] for x in parts),default=0))
  output.append({'raw':vals,'confidence':conf})
 return output

def normalize(r):
 raw=r['raw'];v=[re.sub(r'\s+','',s) for s in raw];out=list(v);issues=[]
 for c in (0,6):
  if re.fullmatch(r'\d+',v[c]):out[c]=int(v[c])
  else:out[c]=None;issues.append((c,'invalid_integer'))
 value=v[4]
 if re.fullmatch(r'\d+(?:\.\d{1,2})?',value):
  from decimal import Decimal
  out[4]=int(Decimal(value)*100)
 else:out[4]=None;issues.append((4,'invalid_money'))
 for c in (5,7):
  m=re.fullmatch(r'(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})',v[c])
  try:out[c]=date(*map(int,m.groups())).isoformat() if m else None
  except ValueError:out[c]=None
  if out[c] is None:issues.append((c,'invalid_date'))
 for c in (1,2,3):
  if not v[c]:issues.append((c,'empty_text'))
 r['values']=out;r['issues']=issues
 return r

if __name__=='__main__':
 import argparse,time
 ap=argparse.ArgumentParser();ap.add_argument('--samples',action='store_true');args=ap.parse_args()
 root=Path('/Volumes/250G/yooli_archive')
 worker=subprocess.Popen(['/tmp/yooli-ocr-work/vision_ocr'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1)
 for batch in [1,50,100,150,200,250,295]:
  m=json.loads((root/f'batch-{batch:04d}'/'metadata.json').read_text())
  for ix in [2,3]:
   image=m['images'][ix-1];path=root/image['path'];g=grid(path)
   if g['kind']!='table':print('GRID_ERROR',batch,ix,g);continue
   temp=Path(f'/tmp/yooli-ocr-work/sample-{batch}-{ix}.png');cv2.imwrite(str(temp),g.pop('clean'))
   start=time.monotonic();worker.stdin.write(str(temp)+'\n');worker.stdin.flush()
   result=json.loads(worker.stdout.readline());rows=[normalize(r) for r in assign(result,g)]
   out={'batch':batch,'image_index':ix,'grid':g,'rows':rows,'seconds':round(time.monotonic()-start,3)}
   Path(f'/tmp/yooli-ocr-work/sample-{batch}-{ix}.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
   print(json.dumps({'batch':batch,'image':ix,'rows':len(rows),'issue_rows':sum(bool(r['issues']) for r in rows),'issues_by_column':{str(c):sum(any(i[0]==c for i in r['issues']) for r in rows) for c in range(8)},'seconds':out['seconds'],'first_rows':[r['values'] for r in rows[:2]]},ensure_ascii=False),flush=True)
 worker.stdin.close();worker.wait()
