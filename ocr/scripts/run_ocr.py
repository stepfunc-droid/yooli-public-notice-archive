import os,json,gzip,csv,re,shutil,sqlite3,hashlib
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
from collections import Counter
import cv2
from cell_engine import CellEngine
from table_ocr import normalize
ROOT=Path('/Volumes/250G/yooli_archive');OUT=ROOT/'tables';OCR=ROOT/'ocr'
CACHE=OCR/'page-cache';DB=OCR/'cells.sqlite'
_ENGINE=None
FIELDS=['批次','序号','姓名或企业名称','身份证号或统一社会信用编码','手机号码','逾期金额_元','借款开始日期','借款期限_月','数据截止日期','公示发布日期','金额_分','金额校验状态','需复核字段','原表未提供字段','原文链接','原图路径','图片序号','图片内行号','图像跨批共用','同批重复原图记录','金额原始识别文本']

def initialize():
 global _ENGINE
 cv2.setNumThreads(1);_ENGINE=CellEngine(DB)

def process_page(item):
 key=Path(item['path']).stem;path=CACHE/(key+'.json.gz')
 if path.exists():
  with gzip.open(path,'rt') as f:old=json.load(f)
  if old.get('pipeline')=='cells-v1' and old.get('source_sha256')==hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest():return {'key':key,'rows':len(old['rows']),'cached':True}
 gray=cv2.imread(str(ROOT/item['path']),0)
 rows=_ENGINE.page(gray,item['grid'])
 cmap=item['grid'].get('column_map',list(range(len(item['grid']['cols']))))
 for row in rows:
  normalize(row)
  if 6 not in cmap:row['issues']=[i for i in row['issues'] if i[0]!=6];row['missing_columns']=[6]
  else:row['missing_columns']=[]
  for c in (0,4,5,6,7):
   if c in row['missing_columns']:continue
   if row['confidence'][c]<.97:row['issues'].append((c,'low_confidence'))
  for c in (1,2,3):
   if row['confidence'][c]<.90:row['issues'].append((c,'low_confidence'))
  for c in (2,3):
   value=row['values'][c]
   if not re.fullmatch(r'[0-9A-Za-z*]+',value):row['issues'].append((c,'unexpected_identifier_characters'))
   if '*' in value and not row['optical_masks'][c]:row['issues'].append((c,'mask_not_optically_verified'))
  if row['values'][6] is not None and not 0<row['values'][6]<=1200:row['issues'].append((6,'invalid_term'))
 obj={'pipeline':'cells-v1','source_image':item['path'],'source_sha256':hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest(),'grid':item['grid'],'rows':rows}
 tmp=path.with_name(path.name+'.tmp')
 with gzip.open(tmp,'wt',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False)
 tmp.replace(path)
 return {'key':key,'rows':len(rows),'cached':False}

COLNAMES=['序号','姓名或企业名称','身份证号或统一社会信用编码','手机号码','逾期金额','借款开始日期','借款期限','数据截止日期']
def format_amount(cents):return '' if cents is None else f'{cents//100}.{cents%100:02d}'

def export_tables(inventory):
 OUT.mkdir(exist_ok=True)
 bypath={x['path']:x for x in inventory['images']}
 db=sqlite3.connect(DB)
 verified={k:(t,float(c or 0)) for k,t,c in db.execute('SELECT key,vision_text,vision_confidence FROM cells WHERE vision_text IS NOT NULL')}
 reviews_path=OCR/'money-source-reviews.json'
 manual={x['key']:x for x in json.loads(reviews_path.read_text())} if reviews_path.exists() else {}
 contexts_path=OCR/'name-context-checks.json'
 contexts=json.loads(contexts_path.read_text()) if contexts_path.exists() else {}
 summaries=[];issues_all=[];page_issues=[]
 publication=[]
 for batch in range(1,296):
  meta=json.loads((ROOT/f'batch-{batch:04d}'/'metadata.json').read_text())
  out=[];expected=1;seq_errors=[];allrows=[];pages=0;seen_images=set();seen_serials=Counter()
  for im in meta['images']:
   item=bypath[im['path']]
   if item['grid']['kind']!='table':continue
   repeated_image=im['sha256'] in seen_images;seen_images.add(im['sha256'])
   pages+=1;key=Path(im['path']).stem
   cp=CACHE/(key+'.json.gz')
   if not cp.exists():page_issues.append({'batch':batch,'image':im['index'],'error':'page_cache_missing'});continue
   with gzip.open(cp,'rt') as f:obj=json.load(f)
   for ri,row in enumerate(obj['rows'],1):
    v=row['values'];issues=[list(x) for x in row['issues']]
    if v[0]!=expected:
     issues.append([0,'sequence_mismatch']);seq_errors.append({'expected':expected,'recognized':v[0],'image':im['index'],'row':ri})
    expected=v[0]+1 if v[0] is not None else expected+1
    if v[0] is not None:seen_serials[v[0]]+=1
    money=row['cell_keys'][4];vs=verified.get(money)
    money_state='待交叉校验'
    if money in manual:
     money_state='原图复核一致'
    elif v[4] is None and re.fullmatch(r'[＃#]+',row['raw'][4]):
     money_state='原图金额不可读'
     issues=[i for i in issues if i[0]!=4]+[[4,'source_amount_overflow']]
    elif vs:
     text=re.sub(r'\s+','',vs[0]);nr=normalize({'raw':['1','x','1','1',text,'2020/1/1','1','2020/1/1'],'confidence':[1]*8})
     vc=nr['values'][4]
     if vc==v[4] and vc is not None:money_state='双引擎一致'
     else:money_state='金额待复核';issues.append([4,'engine_disagreement'])
    elif v[4] is None:money_state='金额未识别'
    for c in (0,4,5,6,7):
     if c in row['missing_columns']:continue
     check=verified.get(row['cell_keys'][c])
     if c==4:
      agrees=money_state in ('双引擎一致','原图复核一致')
     elif check:
      tempraw=['1','x','1','1','1.00','2020/1/1','1','2020/1/1'];tempraw[c]=check[0]
      agrees=normalize({'raw':tempraw,'confidence':[1]*8})['values'][c]==v[c] and v[c] is not None
     else:agrees=False
     if agrees:issues=[i for i in issues if not (i[0]==c and i[1]=='low_confidence')]
    if row['cell_keys'][1] in contexts:issues=[i for i in issues if not (i[0]==1 and i[1]=='low_confidence')]
    elif 1 not in row['missing_columns']:
     namecheck=verified.get(row['cell_keys'][1])
     if namecheck:
      rawname=v[1].rstrip('*') if row['optical_masks'][1] and v[1].endswith('*') else v[1]
      if rawname==namecheck[0].strip() and rawname:issues=[i for i in issues if not (i[0]==1 and i[1]=='low_confidence')]
    if repeated_image:issues.append([0,'source_image_repeated_within_batch'])
    labels=';'.join(sorted(set(COLNAMES[c]+':'+reason for c,reason in issues)))
    out.append([batch,v[0],v[1],v[2],v[3],format_amount(v[4]),v[5],v[6],v[7],meta['published_at'][:10],v[4],money_state,labels,';'.join(COLNAMES[c] for c in row['missing_columns']),meta['source_url'],im['path'],im['index'],ri,len({x['batch'] for x in item['references']})>1,repeated_image,row['raw'][4]])
    if issues:issues_all.append({'batch':batch,'serial':v[0],'image_index':im['index'],'image_row':ri,'source_image':im['path'],'issues':issues,'raw':row['raw'],'confidence':row['confidence']})
    allrows.append(row)
  target=OUT/f'batch-{batch:04d}.csv';temp=target.with_suffix('.csv.tmp')
  with temp.open('w',encoding='utf-8-sig',newline='') as f:
   w=csv.writer(f);w.writerow(FIELDS);w.writerows(out)
  temp.replace(target)
  summaries.append({'batch':batch,'rows':len(out),'table_images':pages,'sequence_issue_rows':len(seq_errors),'invalid_amount_rows':sum(r['values'][4] is None for r in allrows),'review_rows':sum(bool(r[12]) for r in out),'money_verified_rows':sum(r[11] in ('双引擎一致','原图复核一致') for r in out),'money_disagreement_rows':sum(r[11]=='金额待复核' for r in out),'csv':target.name,'source_url':meta['source_url'],'sequence_details':seq_errors,'same_batch_repeated_rows':sum(bool(x[-2]) for x in out),'missing_source_serials':sorted(set(range(1,max(seen_serials,default=0)+1))-set(seen_serials)),'duplicate_source_serials':{k:v for k,v in seen_serials.items() if v>1},'unknown_source_amount_rows':sum(x[11]=='原图金额不可读' for x in out)})
 (OCR/'publication-anomalies.json').write_text(json.dumps([x for x in summaries if x['same_batch_repeated_rows'] or x['missing_source_serials'] or x['unknown_source_amount_rows']],ensure_ascii=False,indent=2))
 (OUT/'batch-list.json').write_text(json.dumps({'batches':summaries,'page_issues':page_issues},ensure_ascii=False,indent=2))
 with gzip.open(OCR/'review-issues.json.gz','wt',encoding='utf-8') as f:json.dump(issues_all,f,ensure_ascii=False)
 (OUT/'README.md').write_text('''# 公示 OCR 表格\n\n每批一份 UTF-8 BOM CSV，可用 Excel 或 Python 读取。列包括原表字段、批次、发布日期、金额整数分、校验状态及原图定位。第214批原图缺少借款期限，留空并标明原表未提供。\n\n第254批第298、327条原图金额为 `########`，金额值留空，不能视作0或声称已得到包括它们的精确总金额。原文还存在重复图片和序号缺口，见 `../ocr/publication-anomalies.json`。

金额计算应使用 `金额_分`，避免浮点误差。人数首先是公示记录数；身份证和姓名有掩码，不能据此保证跨批唯一人数。`同批重复原图记录` 标记原文重复引用同一页的第二次及以后记录，后续统计可明确选择是否排除；`图像跨批共用` 标记原文复用同一图片的情况，保留全部发布记录。OCR 不是人工逐行校对；`需复核字段` 和 `金额校验状态` 说明识别异常或双引擎核对结果。\n\n当前表格生成过程和异常结果见 `batch-list.json` 及 `../ocr/review-issues.json.gz`。原始网页和图片保留在父目录。\n''',encoding='utf-8')
 print(json.dumps({'stage':'export','batches':len(summaries),'rows':sum(x['rows'] for x in summaries),'invalid_amount_rows':sum(x['invalid_amount_rows'] for x in summaries),'sequence_issue_rows':sum(x['sequence_issue_rows'] for x in summaries),'review_rows':len(issues_all),'page_issues':len(page_issues)}),flush=True)
 return summaries

def main():
 OCR.mkdir(exist_ok=True);CACHE.mkdir(exist_ok=True);OUT.mkdir(exist_ok=True)
 inv=json.loads((OCR/'inventory.json').read_text());pages=[x for x in inv['images'] if x['grid']['kind']=='table']
 pages.sort(key=lambda x:(x['references'][0]['batch'],x['references'][0]['image_index']))
 completed=0;rows=0
 workers=min(8,max(2,(os.cpu_count() or 8)//2));print(json.dumps({'stage':'start','pages':len(pages),'workers':workers}),flush=True)
 with ProcessPoolExecutor(max_workers=workers,initializer=initialize) as pool:
  tasks={pool.submit(process_page,p):p for p in pages}
  for task in as_completed(tasks):
   item=tasks[task]
   try:result=task.result();completed+=1;rows+=result['rows']
   except Exception as e:print('PAGE_ERROR',item['path'],repr(e),flush=True);continue
   if completed%100==0 or completed==len(pages):
    status={'stage':'ocr','pages_done':completed,'pages_total':len(pages),'rows_done':rows}
    (OCR/'progress.json').write_text(json.dumps(status,ensure_ascii=False,indent=2));print(json.dumps(status),flush=True)
 export_tables(inv)

if __name__=='__main__':main()
