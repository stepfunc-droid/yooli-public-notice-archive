import argparse
import calendar
import collections
import csv
import datetime as dt
import json
from pathlib import Path


def metric():
    return {'records': 0, 'known_amount_cents': 0, 'unknown_amount_records': 0}


def add_metric(target, cents):
    target['records'] += 1
    if cents is None:
        target['unknown_amount_records'] += 1
    else:
        target['known_amount_cents'] += cents


def money(cents):
    return f'{cents / 100:.2f}'


def pct(part, total):
    return 0.0 if not total else round(part * 100.0 / total, 4)


def add_months(value, months):
    month_index = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def high_confidence(row):
    review = row.get('需复核字段', '') or ''
    missing = row.get('原表未提供字段', '') or ''
    bad_tokens = ('借款开始日期', '借款期限')
    return not any(token in review or token in missing for token in bad_tokens)


def strict_key(row, cents):
    # 仅作为“疑似同一公示记录”的严格指纹。身份证、手机号均被掩码，
    # 因而不能把该指纹当成法律意义上的唯一借款人/唯一债权 ID。
    return (
        row.get('姓名或企业名称', ''),
        row.get('身份证号或统一社会信用编码', ''),
        row.get('手机号码', ''),
        row.get('借款开始日期', ''),
        row.get('借款期限_月', ''),
        cents,
    )


def new_view():
    return {
        'total': metric(),
        'matured_by_cutoff': collections.defaultdict(metric),
        'maturity_buckets': collections.defaultdict(metric),
        'by_start_year': collections.defaultdict(metric),
        'matured_by_2019_10_31_by_start_year': collections.defaultdict(metric),
        'matured_by_2019_10_31_by_term': collections.defaultdict(metric),
    }


def bucket_for(maturity):
    if maturity <= dt.date(2018, 12, 31):
        return 'matured_by_2018_12_31'
    if maturity <= dt.date(2019, 10, 31):
        return 'matured_2019_01_01_to_2019_10_31'
    if maturity <= dt.date(2019, 12, 31):
        return 'matured_2019_11_01_to_2019_12_31'
    if maturity <= dt.date(2020, 6, 30):
        return 'matured_2020_01_01_to_2020_06_30'
    return 'matured_after_2020_06_30'


def add_record_to_view(view, start, term, maturity, cents):
    add_metric(view['total'], cents)
    add_metric(view['by_start_year'][start.year], cents)
    for label, cutoff in CUTOFFS.items():
        if maturity <= cutoff:
            add_metric(view['matured_by_cutoff'][label], cents)
    add_metric(view['maturity_buckets'][bucket_for(maturity)], cents)
    if maturity <= CUTOFFS['2019-10-31']:
        add_metric(view['matured_by_2019_10_31_by_start_year'][start.year], cents)
        add_metric(view['matured_by_2019_10_31_by_term'][term], cents)


CUTOFFS = {
    '2019-10-31': dt.date(2019, 10, 31),
    '2019-12-31': dt.date(2019, 12, 31),
    '2020-06-30': dt.date(2020, 6, 30),
}

BUCKET_ORDER = [
    'matured_by_2018_12_31',
    'matured_2019_01_01_to_2019_10_31',
    'matured_2019_11_01_to_2019_12_31',
    'matured_2020_01_01_to_2020_06_30',
    'matured_after_2020_06_30',
]


def normalize_view(view):
    total = view['total']
    out = {'total': total}
    out['matured_by_cutoff'] = {}
    for label in CUTOFFS:
        v = view['matured_by_cutoff'].get(label, metric())
        out['matured_by_cutoff'][label] = {
            **v,
            'record_pct_of_total': pct(v['records'], total['records']),
            'known_amount_pct_of_total': pct(v['known_amount_cents'], total['known_amount_cents']),
        }
    out['maturity_buckets'] = []
    for label in BUCKET_ORDER:
        v = view['maturity_buckets'].get(label, metric())
        out['maturity_buckets'].append({
            'bucket': label,
            **v,
            'record_pct_of_total': pct(v['records'], total['records']),
            'known_amount_pct_of_total': pct(v['known_amount_cents'], total['known_amount_cents']),
        })
    out['by_start_year'] = [
        {'year': year, **v}
        for year, v in sorted(view['by_start_year'].items())
    ]
    out['matured_by_2019_10_31_by_start_year'] = [
        {'year': year, **v}
        for year, v in sorted(view['matured_by_2019_10_31_by_start_year'].items())
    ]
    out['matured_by_2019_10_31_by_term'] = [
        {'term_months': term, **v}
        for term, v in sorted(view['matured_by_2019_10_31_by_term'].items())
    ]
    return out


def build(root):
    all_view = new_view()
    high_conf_view = new_view()
    raw_after_same_batch_filter = metric()
    parse_failures = []
    low_confidence_count = 0

    strict = {}
    strict_multi_batch_keys = 0

    table_paths = sorted((root / 'tables').glob('batch-[0-9][0-9][0-9][0-9].csv'))
    if len(table_paths) != 295:
        raise SystemExit(f'expected 295 batch tables, found {len(table_paths)}')

    for path in table_paths:
        with path.open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if row.get('同批重复原图记录') == 'True':
                    continue
                cents = int(row['金额_分']) if row.get('金额_分') else None
                add_metric(raw_after_same_batch_filter, cents)
                try:
                    start = dt.date.fromisoformat(row['借款开始日期'])
                    term = int(row['借款期限_月'])
                    if term <= 0:
                        raise ValueError('non-positive term')
                    maturity = add_months(start, term)
                except Exception as exc:
                    parse_failures.append({
                        'batch': row.get('批次'),
                        'serial': row.get('序号'),
                        'loan_start': row.get('借款开始日期'),
                        'term_months': row.get('借款期限_月'),
                        'error': str(exc),
                    })
                    continue

                hc = high_confidence(row)
                if not hc:
                    low_confidence_count += 1

                add_record_to_view(all_view, start, term, maturity, cents)
                if hc:
                    add_record_to_view(high_conf_view, start, term, maturity, cents)

                key = strict_key(row, cents)
                batch = int(row['批次'])
                entry = strict.get(key)
                if entry is None:
                    strict[key] = {
                        'count': 1,
                        'batches': {batch},
                        'start': start,
                        'term': term,
                        'maturity': maturity,
                        'cents': cents,
                        'high_confidence': hc,
                    }
                else:
                    entry['count'] += 1
                    entry['batches'].add(batch)
                    if entry['start'] != start or entry['term'] != term or entry['maturity'] != maturity:
                        raise AssertionError('strict fingerprint inconsistent')

    # 严格指纹视图：每个完全相同的掩码身份 + 日期 + 期限 + 金额组合只计一次。
    # 这只能用于测量“可见的疑似跨批重复”敏感性，不能替代真正唯一债权 ID。
    strict_view = new_view()
    strict_high_conf_view = new_view()
    exact_repeat = metric()
    multi_batch = {
        'keys': 0,
        'records_in_those_keys': 0,
        'extra_occurrences_beyond_first': 0,
        'known_amount_cents_of_extra_occurrences': 0,
        'unknown_amount_extra_occurrences': 0,
        'max_batches_for_one_key': 0,
        'max_occurrences_for_one_key': 0,
    }
    examples = []

    for key, entry in strict.items():
        add_record_to_view(strict_view, entry['start'], entry['term'], entry['maturity'], entry['cents'])
        if entry['high_confidence']:
            add_record_to_view(strict_high_conf_view, entry['start'], entry['term'], entry['maturity'], entry['cents'])
        if len(entry['batches']) > 1:
            multi_batch['keys'] += 1
            multi_batch['records_in_those_keys'] += entry['count']
            extra = entry['count'] - 1
            multi_batch['extra_occurrences_beyond_first'] += extra
            if entry['cents'] is None:
                multi_batch['unknown_amount_extra_occurrences'] += extra
            else:
                multi_batch['known_amount_cents_of_extra_occurrences'] += extra * entry['cents']
            multi_batch['max_batches_for_one_key'] = max(multi_batch['max_batches_for_one_key'], len(entry['batches']))
            multi_batch['max_occurrences_for_one_key'] = max(multi_batch['max_occurrences_for_one_key'], entry['count'])
            if len(examples) < 20:
                examples.append({
                    'masked_name': key[0],
                    'masked_id': key[1],
                    'masked_phone': key[2],
                    'loan_start': key[3],
                    'term_months': key[4],
                    'amount_cents': key[5],
                    'occurrences': entry['count'],
                    'batches': sorted(entry['batches']),
                    'computed_maturity': entry['maturity'].isoformat(),
                })

    all_norm = normalize_view(all_view)
    hc_norm = normalize_view(high_conf_view)
    strict_norm = normalize_view(strict_view)
    strict_hc_norm = normalize_view(strict_high_conf_view)

    result = {
        'generated_at': dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds'),
        'method': {
            'maturity_date': '借款开始日期 + 借款期限（月），按自然月相加；若目标月份无对应日则取该月最后一日',
            'same_batch_duplicates': '排除 同批重复原图记录=True 的记录，与现有 statistics/summary.json 口径一致',
            'high_confidence': '需复核字段/原表未提供字段未标记借款开始日期或借款期限',
            'strict_fingerprint': '姓名或企业名称(掩码)+身份证/统一信用代码(掩码)+手机号(掩码)+借款开始日期+借款期限+金额；仅用于跨批重复敏感性分析，不等于唯一借款人/唯一债权ID',
        },
        'cutoffs': {k: v.isoformat() for k, v in CUTOFFS.items()},
        'source': {
            'batch_count': len(table_paths),
            'records_after_same_batch_duplicate_filter': raw_after_same_batch_filter,
            'parse_failure_count': len(parse_failures),
            'low_confidence_maturity_fields_count': low_confidence_count,
            'parse_failure_examples': parse_failures[:20],
        },
        'all_records_view': all_norm,
        'high_confidence_records_view': hc_norm,
        'strict_fingerprint_dedup_view': strict_norm,
        'strict_fingerprint_high_confidence_view': strict_hc_norm,
        'cross_batch_exact_fingerprint_repeats': {
            **multi_batch,
            'extra_occurrence_amount_pct_of_all_known_amount': pct(
                multi_batch['known_amount_cents_of_extra_occurrences'],
                all_norm['total']['known_amount_cents'],
            ),
            'examples': examples,
        },
    }

    out_dir = root / 'statistics'
    out_dir.mkdir(exist_ok=True)
    (out_dir / 'maturity-analysis.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )

    with (out_dir / 'maturity-analysis.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['视图', '到期区间', '记录数', '已知逾期金额_元', '记录占比_%', '金额占比_%'])
        views = [
            ('全部公示记录', all_norm),
            ('高置信度到期字段', hc_norm),
            ('严格指纹去重敏感性视图', strict_norm),
            ('严格指纹去重+高置信度', strict_hc_norm),
        ]
        for view_name, view in views:
            for row in view['maturity_buckets']:
                writer.writerow([
                    view_name,
                    row['bucket'],
                    row['records'],
                    money(row['known_amount_cents']),
                    row['record_pct_of_total'],
                    row['known_amount_pct_of_total'],
                ])

    with (out_dir / 'maturity-by-start-year.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['借款开始年份', '全部公示记录数', '全部已知金额_元', '截至2019-10-31已合同到期记录数', '截至2019-10-31已合同到期金额_元'])
        total_by_year = {x['year']: x for x in all_norm['by_start_year']}
        matured_by_year = {x['year']: x for x in all_norm['matured_by_2019_10_31_by_start_year']}
        for year in sorted(total_by_year):
            t = total_by_year[year]
            m = matured_by_year.get(year, metric())
            writer.writerow([year, t['records'], money(t['known_amount_cents']), m['records'], money(m['known_amount_cents'])])

    # 生成便于人工审阅的 Markdown 摘要。
    a = all_norm['matured_by_cutoff']['2019-10-31']
    h = hc_norm['matured_by_cutoff']['2019-10-31']
    s = strict_norm['matured_by_cutoff']['2019-10-31']
    sh = strict_hc_norm['matured_by_cutoff']['2019-10-31']
    lines = [
        '# 严重失信公示贷款到期时间分析',
        '',
        f'> 生成时间：{result["generated_at"]}  ',
        '> 目的：判断后来进入严重失信公示的贷款中，有多少在有利网 2019-10-31 仍公开“逾期为 0”的节点之前，已经按照“借款开始日期 + 借款期限”达到合同到期日。',
        '',
        '## 核心结果',
        '',
        f'- 排除同批重复原图后，共 **{all_norm["total"]["records"]:,}** 条公示记录；可读逾期金额按公示人次累计 **{money(all_norm["total"]["known_amount_cents"])} 元**。',
        f'- 其中计算合同到期日 **不晚于 2019-10-31** 的有 **{a["records"]:,}** 条，占 **{a["record_pct_of_total"]:.2f}%**；对应按公示人次累计可读逾期金额 **{money(a["known_amount_cents"])} 元**，占 **{a["known_amount_pct_of_total"]:.2f}%**。',
        f'- 仅保留“借款开始日期/借款期限”未被标记需复核的高置信度记录后，上述数字为 **{h["records"]:,}** 条、**{money(h["known_amount_cents"])} 元**。',
        f'- 使用严格指纹（掩码身份 + 开始日期 + 期限 + 金额）把完全相同的跨批记录只计一次后，上述数字仍为 **{s["records"]:,}** 条、**{money(s["known_amount_cents"])} 元**；再叠加高置信度筛选后为 **{sh["records"]:,}** 条、**{money(sh["known_amount_cents"])} 元**。',
        '',
        '## 证据含义',
        '',
        '这些记录只能证明：相关贷款按照公示中的起始日期和期限计算，在 2019-10-31 前已经达到合同到期日，之后又进入了严重失信公示。它不能单独证明平台在 2019-10-31 已经把这些贷款全部认定为坏账，也不能排除展期、重组、第三方代偿等情形。',
        '',
        '但如果平台同一时点仍公开“逾期金额 0”，那么这批“合同已到期、后来又成为严重失信”的贷款值得单独解释：当时借款人是否已经违约？如已违约，是否由担保/代偿机制在出借人端吸收？平台内部何时掌握其真实还款状态？',
        '',
        '## 跨批重复敏感性',
        '',
        f'- 严格指纹在多个批次重复出现的组合：**{multi_batch["keys"]:,}** 个。',
        f'- 这些组合产生的“除首次外额外出现次数”：**{multi_batch["extra_occurrences_beyond_first"]:,}** 条，对应已知金额 **{money(multi_batch["known_amount_cents_of_extra_occurrences"])} 元**，约占全部按公示人次累计金额的 **{result["cross_batch_exact_fingerprint_repeats"]["extra_occurrence_amount_pct_of_all_known_amount"]:.2f}%**。',
        '- 因身份证和手机号均为掩码，严格指纹去重只能作为敏感性分析，不能当作真正的唯一借款人/唯一债权去重；反过来，同一债权若后续金额变化，也不会被严格指纹识别为重复。',
        '',
        '## 到期区间（全部公示记录）',
        '',
        '| 到期区间 | 记录数 | 已知金额（元） | 金额占比 |',
        '| --- | ---: | ---: | ---: |',
    ]
    for row in all_norm['maturity_buckets']:
        lines.append(f'| {row["bucket"]} | {row["records"]:,} | {money(row["known_amount_cents"])} | {row["known_amount_pct_of_total"]:.2f}% |')
    lines.extend([
        '',
        '## 口径说明',
        '',
        '- 合同到期日按“借款开始日期 + 借款期限（月）”计算；目标月份没有对应日时取月末。',
        '- 排除 `同批重复原图记录=True`，与现有年度统计口径一致。',
        '- “高置信度”仅表示 OCR/原表字段未把开始日期或期限标记为需复核，不代表其他字段不存在识别误差。',
        '- 97.94 亿元及本文金额均是公示记录按人次累计值；本文另外给出严格指纹去重敏感性视图，但不把它宣称为法律意义上的唯一债权统计。',
        '',
    ])
    (out_dir / 'maturity-analysis.md').write_text('\n'.join(lines), encoding='utf-8')

    print(json.dumps({
        'total_records': all_norm['total']['records'],
        'total_known_amount_cents': all_norm['total']['known_amount_cents'],
        'matured_by_2019_10_31': a,
        'high_conf_matured_by_2019_10_31': h,
        'strict_dedup_matured_by_2019_10_31': s,
        'strict_high_conf_matured_by_2019_10_31': sh,
        'cross_batch_exact_repeat': multi_batch,
        'parse_failures': len(parse_failures),
        'low_confidence': low_confidence_count,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    build(args.root.resolve())
