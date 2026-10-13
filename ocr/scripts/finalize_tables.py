from pathlib import Path
import json,csv,gzip,hashlib,shutil,collections
from decimal import Decimal
from datetime import date
from html import escape
import run_ocr
r=run_ocr.ROOT;o=r/'ocr';out=r/'tables'
previous=o/'initial-derived-reports';previous.mkdir(exist_ok=True)
for name in ('batch-list.json','README.md'):
 src=out/name
 if src.exists() and not (previous/name).exists():shutil.copy2(src,previous/name)
inv=json.loads((o/'inventory.json').read_text());summaries=run_ocr.export_tables(inv)
issues=[];counts=collections.Counter();rows_total=0;bybatch={x['batch']:x for x in summaries};csv_manifest=[]
expected=sum(len(x['grid'].get('rows',[]))*len(x['references']) for x in inv['images'])
for b in range(1,296):
 path=out/f'batch-{b:04d}.csv';preview=[];n=0
 with path.open(encoding='utf-8-sig',newline='') as f:
  reader=csv.DictReader(f)
  if reader.fieldnames!=run_ocr.FIELDS:issues.append({'batch':b,'type':'header_schema'})
  for row in reader:
   n+=1;rows_total+=1
   if len(preview)<30:preview.append(row)
   if int(row['批次'])!=b:issues.append({'batch':b,'row':n,'type':'batch_value'})
   if not row['序号'].isdigit():issues.append({'batch':b,'row':n,'type':'invalid_serial'})
   for c in ('借款开始日期','数据截止日期','公示发布日期'):
    try:date.fromisoformat(row[c])
    except ValueError:issues.append({'batch':b,'row':n,'type':'invalid_date','column':c})
   if row['金额_分']:
    cents=int(row['金额_分'])
    if Decimal(row['逾期金额_元'])*100!=cents:issues.append({'batch':b,'row':n,'type':'amount_roundtrip'})
    if row['金额校验状态'] not in ('双引擎一致','原图复核一致'):issues.append({'batch':b,'row':n,'type':'amount_not_verified'})
    counts['known_amount_rows']+=1
   else:
    counts['unknown_amount_rows']+=1
    if b!=254 or int(row['序号']) not in (298,327) or row['金额校验状态']!='原图金额不可读':issues.append({'batch':b,'row':n,'type':'unexpected_missing_amount'})
   if row['同批重复原图记录']=='True':counts['repeated_image_rows']+=1
   if row['需复核字段']:counts['review_rows']+=1
   if not row['姓名或企业名称']:counts['source_blank_name_rows']+=1
   if not row['借款期限_月']:
    counts['source_missing_term_rows']+=1
    if b!=214:issues.append({'batch':b,'row':n,'type':'unexpected_missing_term'})
   elif not row['借款期限_月'].isdigit():issues.append({'batch':b,'row':n,'type':'invalid_term'})
   if not (r/row['原图路径']).is_file():issues.append({'batch':b,'row':n,'type':'source_image_missing'})
  if n!=bybatch[b]['rows']:issues.append({'batch':b,'type':'row_count_mismatch'})
 csv_manifest.append({'batch':b,'path':path.name,'rows':n,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
 columns=['序号','姓名或企业名称','身份证号或统一社会信用编码','手机号码','逾期金额_元','借款开始日期','借款期限_月','数据截止日期','金额校验状态','需复核字段']
 htmlrows=[]
 for row in preview:
  htmlrows.append('<tr>'+''.join('<td class="'+('num' if c in ('序号','逾期金额_元','借款期限_月') else '')+'">'+escape(row[c])+'</td>' for c in columns)+'</tr>')
 desc=bybatch[b]
 style='body{font:14px system-ui;margin:28px;color:#172b3a}h1{font-size:23px}a{color:#17649b}table{border-collapse:collapse;white-space:nowrap}th{background:#193c53;color:white;padding:10px;text-align:left}td{padding:8px;border-bottom:1px solid #e4e8ec}tr:nth-child(even){background:#f4f7fa}.num{text-align:right}.scroll{overflow:auto}p{line-height:1.7}'
 doc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>第'+str(b)+'批公示表格</title><style>'+style+'</style><a href="index.html">返回总清单</a><h1>第'+str(b)+'批公示表格</h1><p>'+f'完整 CSV 共 {n:,} 条记录。下方仅预览前 {len(preview)} 行。<br><a href="batch-{b:04d}.csv">下载完整 CSV</a> · <a href="../batch-{b:04d}/index.html">查看归档原文与名单图片</a>'+'</p><p>同批重复原图记录：'+str(desc['same_batch_repeated_rows'])+' 条；原图不可读金额：'+str(desc['unknown_source_amount_rows'])+' 条。金额计算使用整数“分”，空值不等于0。</p><div class="scroll"><table><tr>'+''.join('<th>'+escape(c)+'</th>' for c in columns)+'</tr>'+''.join(htmlrows)+'</table></div></html>'
 (out/f'batch-{b:04d}.html').write_text(doc)
if rows_total!=expected:issues.append({'type':'inventory_row_coverage','expected':expected,'actual':rows_total})
report={'batches':295,'table_images':sum(x['grid']['kind']=='table' for x in inv['images']),'raw_published_record_rows':rows_total,'same_batch_repeated_image_rows':counts['repeated_image_rows'],'rows_excluding_repeated_images':rows_total-counts['repeated_image_rows'],'known_amount_rows':counts['known_amount_rows'],'unknown_source_amount_rows':counts['unknown_amount_rows'],'all_readable_amounts_checked':not any(x['type'] in ('amount_not_verified','amount_roundtrip','unexpected_missing_amount') for x in issues),'source_sequence_gap_count':sum(len(x['missing_source_serials']) for x in summaries),'nonempty_review_marker_rows':counts['review_rows'],'source_blank_name_rows':counts['source_blank_name_rows'],'source_missing_term_rows':counts['source_missing_term_rows'],'structural_verification_issues':issues,'csv_manifest':csv_manifest}
(o/'table-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
with (out/'batch-list.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.writer(f);w.writerow(['批次','公示记录数_含重复图片','重复图片记录数','金额可读记录数','原图金额不可读记录数','序号缺口数量','复核标记记录数','CSV文件','原文链接'])
 for x in summaries:w.writerow([x['batch'],x['rows'],x['same_batch_repeated_rows'],x['money_verified_rows'],x['unknown_source_amount_rows'],len(x['missing_source_serials']),x['review_rows'],x['csv'],x['source_url']])
index_rows=''.join(f'<tr><td>{x["batch"]}</td><td>{x["rows"]:,}</td><td>{x["same_batch_repeated_rows"]}</td><td>{len(x["missing_source_serials"])}</td><td>{x["unknown_source_amount_rows"]}</td><td><a href="batch-{x["batch"]:04d}.html">预览</a> · <a href="{x["csv"]}">CSV</a></td></tr>' for x in summaries)
style='body{font:15px system-ui;margin:32px;color:#19313e;max-width:1150px}h1{font-size:28px}p{line-height:1.8}table{border-collapse:collapse;width:100%}th{background:#193c53;color:white;text-align:left;padding:11px}td{padding:9px;border-bottom:1px solid #e1e7eb}tr:nth-child(even){background:#f5f8fa}a{color:#17649b}.note{background:#fff5dc;padding:18px;border-left:4px solid #c58a18}'
index='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>有利网公示OCR表格</title><style>'+style+'</style><h1>有利网公示表格 · 第1—295批</h1><p>'+f'{rows_total:,} 条按原图保留的公示记录；同批重复图片记录 {counts["repeated_image_rows"]} 条。<br>记录数用于统计公示条目或人次，不能据掩码保证跨批唯一人数。'+'</p><div class="note">第254批第298、327条金额在原图中显示为 ########，保留空值；它们不能计为0。第51、134、148批存在重复图片；第134、148、198批存在原文序号缺口。统计前请确认排除重复图片的口径。非金额字段的低置信度结果保留复核标记。</div><p><a href="batch-list.csv">下载批次清单</a> · <a href="README.md">字段与统计口径说明</a> · <a href="../ocr/table-verification.json">校验报告</a> · <a href="../index.html">归档原文</a></p><table><tr><th>批次</th><th>公示记录</th><th>重复图片记录</th><th>序号缺口</th><th>不可读金额</th><th>表格</th></tr>'+index_rows+'</table></html>'
(out/'index.html').write_text(index)
(o/'dataset-schema.json').write_text(json.dumps({'encoding':'UTF-8 with BOM','csv_columns':run_ocr.FIELDS,'amount_computation':'integer cents in 金额_分; never convert unknown to zero','publication_rows':'one output row per visible table data row; preserve repeated source image occurrences and label them','unique_persons':'not derivable reliably from masked identifiers','source_exceptions':{'unreadable_amounts':{'batch':254,'serials':[298,327]},'missing_term_column':214,'source_blank_names':5,'fully_masked_names':1}},ensure_ascii=False,indent=2))
scripts=o/'scripts';scripts.mkdir(exist_ok=True)
for name in ['run_ocr.py','table_ocr.py','cell_engine.py','verify_money.py','recheck_money_sparse.py','finalize_tables.py','vision_ocr.swift']:
 shutil.copy2('/tmp/yooli-ocr-work/'+name,scripts/name)
for log in ['full-ocr.log','money-check.log','money-check-corrected.log','money-sparse.log','other-fields.log','name-check.log','refine-names.log']:
 shutil.copy2('/tmp/yooli-ocr-work/'+log,o/log)
print(json.dumps({k:v for k,v in report.items() if k!='csv_manifest'},ensure_ascii=False),flush=True)
if issues:raise SystemExit(2)
