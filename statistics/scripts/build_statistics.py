import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path


def empty():
    return {'records': 0, 'known_amount_cents': 0, 'unknown_amount_records': 0}


def add(target, cents):
    target['records'] += 1
    if cents is None:
        target['unknown_amount_records'] += 1
    else:
        target['known_amount_cents'] += cents


def money(cents):
    return f'{cents // 100}.{cents % 100:02d}'


def export_csv(path, headers, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


def build(root, template):
    output = root / 'statistics'
    output.mkdir(exist_ok=True)
    years = collections.defaultdict(empty)
    publication_years = collections.defaultdict(empty)
    batches = []
    total = empty()
    raw = empty()
    repeated = empty()
    unknown = []
    sources = []
    dates = []
    for path in sorted((root / 'tables').glob('batch-[0-9][0-9][0-9][0-9].csv')):
        batch_stats = empty()
        batch_years = collections.defaultdict(empty)
        batch_repeated = empty()
        batch_id = int(path.stem.split('-')[1])
        batch_dates = set()
        with path.open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                assert int(row['批次']) == batch_id
                date = dt.date.fromisoformat(row['借款开始日期'])
                published = dt.date.fromisoformat(row['公示发布日期'])
                batch_dates.add(published.isoformat())
                cents = int(row['金额_分']) if row['金额_分'] else None
                if cents is not None:
                    assert Decimal(row['逾期金额_元']) * 100 == cents
                    assert cents >= 0
                add(raw, cents)
                assert row['同批重复原图记录'] in ('True', 'False')
                if row['同批重复原图记录'] == 'True':
                    add(repeated, cents)
                    add(batch_repeated, cents)
                    continue
                add(total, cents)
                add(batch_stats, cents)
                add(years[date.year], cents)
                add(publication_years[published.year], cents)
                add(batch_years[date.year], cents)
                if cents is None:
                    unknown.append({'batch': batch_id, 'serial': int(row['序号']),
                                    'loan_year': date.year, 'source_image': row['原图路径'],
                                    'reason': row['金额校验状态']})
        assert len(batch_dates) == 1
        published = next(iter(batch_dates))
        dates.append(published)
        batches.append({'batch': batch_id, 'publication_date': published,
                        **batch_stats, 'excluded_duplicate_records': batch_repeated['records'],
                        'loan_years': {str(k): v for k, v in sorted(batch_years.items())}})
        sources.append({'csv': f'../tables/{path.name}',
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    for group in [years.values(), publication_years.values(), batches]:
        for field in total:
            assert sum(x[field] for x in group) == total[field]
    for field in total:
        assert raw[field] - repeated[field] == total[field]
    for batch in batches:
        for field in total:
            assert sum(v[field] for v in batch['loan_years'].values()) == batch[field]
    assert len(batches) == 295
    verification = json.loads((root / 'ocr/table-verification.json').read_text())
    assert raw['records'] == verification['raw_published_record_rows']
    assert repeated['records'] == verification['same_batch_repeated_image_rows']
    assert total['records'] == verification['rows_excluding_repeated_images']
    assert total['unknown_amount_records'] == verification['unknown_source_amount_rows']
    data = {'generated_at': dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds'),
            'basis': '借款开始年份；排除同批重复原图记录；跨批按公示人次计数；金额按整数分汇总；未知金额不计入已知合计',
            'total': total, 'raw': raw, 'excluded_duplicates': repeated,
            'batch_count': len(batches), 'publication_date_range': [min(dates), max(dates)],
            'loan_years': [{'year': k, **v} for k, v in sorted(years.items())],
            'publication_years': [{'year': k, **v} for k, v in sorted(publication_years.items())],
            'batches': batches, 'unknown_amounts': unknown,
            'source_sequence_gaps': verification['source_sequence_gap_count']}
    (output / 'summary.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    export_csv(output / 'yearly-summary.csv', ['借款开始年份', '公示逾期人次', '已知逾期金额_元', '已知逾期金额_分', '金额未知人次'],
               [[v['year'], v['records'], money(v['known_amount_cents']), v['known_amount_cents'], v['unknown_amount_records']] for v in data['loan_years']])
    export_csv(output / 'batch-summary.csv', ['批次', '公示发布日期', '公示逾期人次', '已知逾期金额_元', '已知逾期金额_分', '金额未知人次', '排除重复图片记录'],
               [[b['batch'], b['publication_date'], b['records'], money(b['known_amount_cents']), b['known_amount_cents'], b['unknown_amount_records'], b['excluded_duplicate_records']] for b in batches])
    export_csv(output / 'batch-year-summary.csv', ['批次', '公示发布日期', '借款开始年份', '公示逾期人次', '已知逾期金额_元', '已知逾期金额_分', '金额未知人次'],
               [[b['batch'], b['publication_date'], y, v['records'], money(v['known_amount_cents']), v['known_amount_cents'], v['unknown_amount_records']] for b in batches for y, v in b['loan_years'].items()])
    encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    html = template.read_text().replace('__SUMMARY_DATA__', encoded)
    (output / 'index.html').write_text(html)
    report = {'status': 'passed', 'aggregation_checks': ['各借款年份之和等于总计', '各公示年份之和等于总计',
              '各批次之和等于总计', '各批内部年份之和等于该批总计', '原始记录减重复记录等于统计记录',
              '金额元/分逐条一致', '与OCR记录校验报告一致'],
              'source_csvs': sources, 'total': total,
              'output_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.suffix in ('.csv', '.html', '.json') and p.name != 'verification.json'}}
    (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: data[k] for k in ['total', 'excluded_duplicates', 'loan_years', 'publication_date_range']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/Volumes/250G/yooli_archive'))
    parser.add_argument('--template', type=Path, default=Path(__file__).with_name('statistics-template.html'))
    args = parser.parse_args()
    build(args.root, args.template)
