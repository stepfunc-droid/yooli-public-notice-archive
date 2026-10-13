import argparse
import calendar
import collections
import csv
import datetime as dt
import json
from pathlib import Path

CUTOFF = dt.date(2019, 10, 31)
EARLY_CUTOFF = dt.date(2018, 12, 31)


def add_months(value, months):
    month_index = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def metric():
    return {'records': 0, 'known_amount_cents': 0, 'unknown_amount_records': 0}


def add(target, cents, count=1):
    target['records'] += count
    if cents is None:
        target['unknown_amount_records'] += count
    else:
        target['known_amount_cents'] += cents * count


def yi(cents):
    return f'{cents / 10_000_000_000:.4f}'


def yuan(cents):
    return f'{cents / 100:.2f}'


def strict_key(row, cents):
    return (
        row.get('姓名或企业名称', ''),
        row.get('身份证号或统一社会信用编码', ''),
        row.get('手机号码', ''),
        row.get('借款开始日期', ''),
        row.get('借款期限_月', ''),
        cents,
    )


def month_key(date):
    return date.strftime('%Y-%m')


def aggregate(records):
    three_dim = collections.defaultdict(metric)
    by_maturity_year_pub = collections.defaultdict(metric)
    by_maturity_month_pub = collections.defaultdict(metric)
    by_term_pub = collections.defaultdict(metric)
    by_pub_year = collections.defaultdict(metric)
    by_maturity_year = collections.defaultdict(metric)
    early_by_pub_year = collections.defaultdict(metric)
    early_total = metric()
    cutoff_total = metric()

    for r, count in records:
        maturity = r['maturity']
        if maturity > CUTOFF:
            continue
        cents = r['cents']
        pub_year = r['published'].year
        mmonth = month_key(maturity)
        myear = maturity.year
        term = r['term']
        add(cutoff_total, cents, count)
        add(three_dim[(mmonth, term, pub_year)], cents, count)
        add(by_maturity_year_pub[(myear, pub_year)], cents, count)
        add(by_maturity_month_pub[(mmonth, pub_year)], cents, count)
        add(by_term_pub[(term, pub_year)], cents, count)
        add(by_pub_year[pub_year], cents, count)
        add(by_maturity_year[myear], cents, count)
        if maturity <= EARLY_CUTOFF:
            add(early_total, cents, count)
            add(early_by_pub_year[pub_year], cents, count)

    return {
        'cutoff_total': cutoff_total,
        'early_total': early_total,
        'three_dim': three_dim,
        'by_maturity_year_pub': by_maturity_year_pub,
        'by_maturity_month_pub': by_maturity_month_pub,
        'by_term_pub': by_term_pub,
        'by_pub_year': by_pub_year,
        'by_maturity_year': by_maturity_year,
        'early_by_pub_year': early_by_pub_year,
    }


def dict_rows(group, key_names):
    rows = []
    for key, value in sorted(group.items()):
        if not isinstance(key, tuple):
            key = (key,)
        rows.append({**dict(zip(key_names, key)), **value})
    return rows


def export_csv(path, headers, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def build(root):
    paths = sorted((root / 'tables').glob('batch-[0-9][0-9][0-9][0-9].csv'))
    if len(paths) != 295:
        raise SystemExit(f'expected 295 batch tables, found {len(paths)}')

    source = metric()
    parse_failures = metric()
    parse_failure_batches = collections.Counter()
    parsed = []
    strict_groups = {}

    for path in paths:
        with path.open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if row.get('同批重复原图记录') == 'True':
                    continue
                cents = int(row['金额_分']) if row.get('金额_分') else None
                add(source, cents)
                batch = int(row['批次'])
                try:
                    start = dt.date.fromisoformat(row['借款开始日期'])
                    term = int(row['借款期限_月'])
                    published = dt.date.fromisoformat(row['公示发布日期'])
                    if term <= 0:
                        raise ValueError('non-positive term')
                    maturity = add_months(start, term)
                except Exception:
                    add(parse_failures, cents)
                    parse_failure_batches[batch] += 1
                    continue

                record = {
                    'batch': batch,
                    'start': start,
                    'term': term,
                    'maturity': maturity,
                    'published': published,
                    'cents': cents,
                }
                parsed.append(record)

                key = strict_key(row, cents)
                group = strict_groups.setdefault(key, {'record_by_batch': {}, 'counts': collections.Counter()})
                group['counts'][batch] += 1
                if batch not in group['record_by_batch']:
                    group['record_by_batch'][batch] = record

    raw_records = [(r, 1) for r in parsed]
    raw = aggregate(raw_records)

    # 保守跨批严格指纹视图：每个指纹只保留单一批次中最大的出现次数；
    # 如多个批次并列，保留公示日期最早的批次，以避免人为把“首次公示时间”推迟。
    conservative_records = []
    repeated_keys = 0
    removed_occurrences = 0
    removed_cents = 0
    for group in strict_groups.values():
        counts = group['counts']
        max_count = max(counts.values())
        candidates = [b for b, c in counts.items() if c == max_count]
        chosen_batch = min(candidates, key=lambda b: (group['record_by_batch'][b]['published'], b))
        chosen_record = group['record_by_batch'][chosen_batch]
        conservative_records.append((chosen_record, max_count))
        if len(counts) > 1:
            repeated_keys += 1
            removed = sum(counts.values()) - max_count
            removed_occurrences += removed
            if chosen_record['cents'] is not None:
                removed_cents += removed * chosen_record['cents']

    conservative = aggregate(conservative_records)

    out = root / 'statistics'
    out.mkdir(exist_ok=True)

    export_csv(
        out / 'maturity-month-term-publication-matrix.csv',
        ['最终到期月', '借款期限_月', '后来公示年份', '公示记录数', '已知逾期金额_元', '已知逾期金额_分', '金额未知记录数'],
        [[m, term, pub, v['records'], yuan(v['known_amount_cents']), v['known_amount_cents'], v['unknown_amount_records']]
         for (m, term, pub), v in sorted(raw['three_dim'].items())]
    )
    export_csv(
        out / 'maturity-month-publication-matrix.csv',
        ['最终到期月', '后来公示年份', '公示记录数', '已知逾期金额_元', '已知逾期金额_分', '金额未知记录数'],
        [[m, pub, v['records'], yuan(v['known_amount_cents']), v['known_amount_cents'], v['unknown_amount_records']]
         for (m, pub), v in sorted(raw['by_maturity_month_pub'].items())]
    )
    export_csv(
        out / 'maturity-year-publication-matrix.csv',
        ['最终到期年份', '后来公示年份', '公示记录数', '已知逾期金额_元', '已知逾期金额_分', '金额未知记录数'],
        [[year, pub, v['records'], yuan(v['known_amount_cents']), v['known_amount_cents'], v['unknown_amount_records']]
         for (year, pub), v in sorted(raw['by_maturity_year_pub'].items())]
    )
    export_csv(
        out / 'maturity-term-publication-matrix.csv',
        ['借款期限_月', '后来公示年份', '公示记录数', '已知逾期金额_元', '已知逾期金额_分', '金额未知记录数'],
        [[term, pub, v['records'], yuan(v['known_amount_cents']), v['known_amount_cents'], v['unknown_amount_records']]
         for (term, pub), v in sorted(raw['by_term_pub'].items())]
    )

    raw_cutoff = raw['cutoff_total']
    raw_early = raw['early_total']
    cons_cutoff = conservative['cutoff_total']
    cons_early = conservative['early_total']

    result = {
        'generated_at': dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds'),
        'method': {
            'scope': '排除同批重复原图记录；仅统计可计算最终合同到期日的记录',
            'maturity_date': '借款开始日期 + 借款期限（月）；目标月无对应日则取月末',
            'main_cutoff': CUTOFF.isoformat(),
            'early_cutoff': EARLY_CUTOFF.isoformat(),
            'strict_fingerprint_sensitivity': '掩码姓名+掩码身份证/统一信用代码+掩码手机号+借款开始日期+借款期限+金额；跨批只保留单一批次最大出现次数，并在并列时选择最早公示批次',
        },
        'source': {
            'all_records_after_same_batch_filter': source,
            'parse_failures': parse_failures,
            'parse_failure_batches': dict(sorted(parse_failure_batches.items())),
            'parseable_records': len(parsed),
        },
        'raw_publication_person_time_view': {
            'matured_by_2019_10_31': raw_cutoff,
            'matured_by_2018_12_31': raw_early,
            'matured_by_2019_10_31_by_publication_year': dict_rows(raw['by_pub_year'], ['publication_year']),
            'matured_by_2018_12_31_by_publication_year': dict_rows(raw['early_by_pub_year'], ['publication_year']),
            'by_maturity_year_publication_year': dict_rows(raw['by_maturity_year_pub'], ['maturity_year', 'publication_year']),
            'by_term_publication_year': dict_rows(raw['by_term_pub'], ['term_months', 'publication_year']),
        },
        'conservative_strict_fingerprint_view': {
            'matured_by_2019_10_31': cons_cutoff,
            'matured_by_2018_12_31': cons_early,
            'matured_by_2019_10_31_by_publication_year': dict_rows(conservative['by_pub_year'], ['publication_year']),
            'matured_by_2018_12_31_by_publication_year': dict_rows(conservative['early_by_pub_year'], ['publication_year']),
            'repeated_multi_batch_keys': repeated_keys,
            'removed_cross_batch_occurrences': removed_occurrences,
            'removed_known_amount_cents': removed_cents,
        },
    }
    (out / 'maturity-matrix.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    # Markdown 重点展示“到期年份 × 后来公示年份”与“2018年底前已到期”的公示时间分布。
    maturity_years = sorted({y for y, _ in raw['by_maturity_year_pub']})
    pub_years = sorted({p for _, p in raw['by_maturity_year_pub']})
    year_matrix = {(y, p): raw['by_maturity_year_pub'].get((y, p), metric()) for y in maturity_years for p in pub_years}

    lines = [
        '# 严重失信贷款：最终到期时间 × 期限 × 后来公示时间矩阵', '',
        f'> 生成时间：{result["generated_at"]}  ',
        '> 目的：进一步穿透“2019-10-31 仍披露逾期为 0”这一节点，观察后来进入严重失信公示的贷款，在原始合同最终到期后隔了多久、到哪一年才被公开列入严重失信名单。', '',
        '## 核心结果', '',
        f'- 2019-10-31 前已最终到期：**{raw_cutoff["records"]:,} 条、{yi(raw_cutoff["known_amount_cents"])} 亿元**（公示人次口径）。',
        f'- 其中 2018-12-31 前已最终到期：**{raw_early["records"]:,} 条、{yi(raw_early["known_amount_cents"])} 亿元**。',
        f'- 保守严格指纹跨批去重后，2019-10-31 前已最终到期仍有 **{cons_cutoff["records"]:,} 条、{yi(cons_cutoff["known_amount_cents"])} 亿元**；2018 年底前已最终到期仍有 **{cons_early["records"]:,} 条、{yi(cons_early["known_amount_cents"])} 亿元**。', '',
        '## 2018 年底前已经最终到期的贷款，后来在哪一年才被严重失信公示', '',
        '| 后来公示年份 | 记录数 | 公示人次累计逾期金额（亿元） |',
        '| --- | ---: | ---: |',
    ]
    for pub, v in sorted(raw['early_by_pub_year'].items()):
        lines.append(f'| {pub} | {v["records"]:,} | {yi(v["known_amount_cents"])} |')

    lines.extend(['', '这张表的意义是：这些贷款按原始合同在 **2018 年底之前已经走到最终到期日**，但严重失信公示发生在 2020—2024 年。它不能证明平台在最终到期日当天已经确认坏账，但能够把“合同尚未到期所以当时无法观察履约结果”这一解释排除掉。', '',
                  '## 最终到期年份 × 后来公示年份（金额，亿元）', ''])
    header = '| 最终到期年份 | ' + ' | '.join(str(p) for p in pub_years) + ' | 合计 |'
    sep = '| --- | ' + ' | '.join('---:' for _ in pub_years) + ' | ---: |'
    lines.extend([header, sep])
    for y in maturity_years:
        vals = [year_matrix[(y, p)]['known_amount_cents'] for p in pub_years]
        lines.append('| ' + str(y) + ' | ' + ' | '.join(yi(v) for v in vals) + f' | {yi(sum(vals))} |')

    lines.extend(['', '## 2019-10-31 前已最终到期贷款：按期限与后来公示年份拆分', '',
                  '| 期限（月） | 公示年份 | 记录数 | 金额（亿元） |',
                  '| ---: | ---: | ---: | ---: |'])
    for (term, pub), v in sorted(raw['by_term_pub'].items()):
        lines.append(f'| {term} | {pub} | {v["records"]:,} | {yi(v["known_amount_cents"])} |')

    lines.extend(['', '## 可复核的完整矩阵文件', '',
                  '- `maturity-month-term-publication-matrix.csv`：最终到期月 × 借款期限 × 后来公示年份（三维长表）。',
                  '- `maturity-month-publication-matrix.csv`：最终到期月 × 后来公示年份。',
                  '- `maturity-year-publication-matrix.csv`：最终到期年份 × 后来公示年份。',
                  '- `maturity-term-publication-matrix.csv`：借款期限 × 后来公示年份。',
                  '- `maturity-matrix.json`：核心汇总和保守严格指纹敏感性视图。', '',
                  '## 证据边界', '',
                  '- “最终合同到期”不等于“当日已经确认坏账”。仍可能存在展期、重组、第三方代偿等安排。',
                  '- 对分期贷款而言，最终合同到期日晚于每一期应还款日，因此用最终到期日判断历史风险是偏保守的。',
                  '- 所有金额仍是官方严重失信公示记录按公示人次累计；严格指纹仅用于跨批重复敏感性分析，不等于唯一债权 ID 去重。',
                  ''])
    (out / 'maturity-matrix.md').write_text('\n'.join(lines), encoding='utf-8')

    print(json.dumps({
        'matured_by_2019_10_31': raw_cutoff,
        'matured_by_2018_12_31': raw_early,
        'early_by_publication_year': {str(k): v for k, v in sorted(raw['early_by_pub_year'].items())},
        'conservative_matured_by_2019_10_31': cons_cutoff,
        'conservative_matured_by_2018_12_31': cons_early,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    build(args.root.resolve())
