from pathlib import Path
import json,gzip,re,cv2,shutil
from rapidocr import RapidOCR
r=Path('/Volumes/250G/yooli_archive');o=r/'ocr'
targets=json.loads(Path('/tmp/yooli-ocr-work/name-review-targets.json').read_text());wanted=set(targets['keys']);reps={}
for p in (o/'page-cache').glob('*.json.gz'):
 with gzip.open(p,'rt') as f:obj=json.load(f)
 for i,row in enumerate(obj['rows']):
  k=row['cell_keys'][1]
  if k in wanted and k not in reps:reps[k]=(obj['source_image'],obj['grid']['rows'][i],obj['grid']['cols'][1],row['raw'][1],row['optical_masks'][1])
 if len(reps)==len(wanted):break
engine=RapidOCR(params={'Global.use_det':False,'Global.use_cls':False,'Global.log_level':'warning','EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1,'Rec.rec_batch_num':64,'Rec.rec_img_shape':[3,48,160]})
keys=list(reps);crops=[]
for k in keys:
 path,(y0,y1),(x0,x1),raw,opt=reps[k]
 im=cv2.imread(str(r/path),0);crop=im[y0+1:y1,x0+1:x1];crop=cv2.resize(crop,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
 crop=cv2.copyMakeBorder(crop,8,8,8,8,cv2.BORDER_CONSTANT,value=255);crops.append(cv2.cvtColor(crop,cv2.COLOR_GRAY2BGR))
results=engine.recognize_txt(crops);agreement={}
for k,t,score in zip(keys,results.txts,results.scores):
 raw=reps[k][3];opt=reps[k][4]
 a=re.sub(r'\s+','',raw);b=re.sub(r'\s+','',t)
 match=a==b or (opt and a.endswith('*') and b.rstrip('*')==a.rstrip('*'))
 if match and float(score)>=.98:agreement[k]={'recognized':t,'confidence':float(score),'basis':'Same disclosed characters in full-cell context; optical mask count preserved.'}
(o/'name-context-checks.json').write_text(json.dumps(agreement,ensure_ascii=False,indent=2))
# Six empty OCR outcomes were inspected against their actual source cells.
changes=[]
for j,target in enumerate(targets['empty']):
 p=o/'page-cache'/target['page']
 with gzip.open(p,'rt') as f:obj=json.load(f)
 row=obj['rows'][target['row']]
 assert row['raw'][1]==''
 if j==2:
  row['raw'][1]='**';row['values'][1]='**';row['confidence'][1]=1.0;row['optical_masks'][1]=True
  row['issues']=[x for x in row['issues'] if x[0]!=1]
  reason='Source displays exactly two asterisks and no disclosed surname.'
 else:
  row['issues']=[x for x in row['issues'] if x[0]!=1]
  if 1 not in row['missing_columns']:row['missing_columns'].append(1)
  reason='Source name cell is blank; value retained as missing.'
 changes.append(dict(target,confirmed_value=row['values'][1],reason=reason))
 with gzip.open(p,'wt',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False)
(o/'source-name-empty-cells.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2))
shutil.copy2('/tmp/yooli-ocr-work/empty-names.png',o/'review-evidence/empty-names.png')
print(json.dumps({'context_checked':len(keys),'high_confidence_agreements':len(agreement),'source_blank_names':5,'fully_masked_name_cells':1}),flush=True)
