import argparse
import calendar
import collections
import csv
import datetime as dt
import json
from pathlib import Path


def metric():
    return {'records': 0, 'known_amount_cents': 0, 'unknown_amount_records': 0}


def add_metric(target, cents, count=1):
    target['records'] += count
    if cents is None:
        target['unknown_amount_records'] += count
    else:
        target['known_amount_cents'] += cents * count


def money(cents):
    return f'{cents / 100:.2f}'


def yi(cents):
    return f'{cents / 10_000_000_000:.4f}'


def pct(part, total):
    return 0.0 if not total else round(part * 100.0 / total, 4)


def add_months(value, months):
    month_index = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def maturity_fields_high_confidence(row):
    review = row.get('需复核字段', '') or ''
    missing = row.get('原表未提供字段', '') or ''
    return not any(token in review or token in missing for token in ('借款开始日期', '借款期限'))


def strict_key(row, cents):
    return (
        row.get('姓名或企业名称', ''),
        row.get('身份证号或统一社会信用编码', ''),
        row.get('手机号码', ''),
        row.get('借款开始日期', ''),
        row.get('借款期限_月', ''),
        cents,
    )


CUTOFFS = {
    '2018-12-31': dt.date(2018, 12, 31),
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


def bucket_for(maturity):
    if maturity <= CUTOFFS['2018-12-31']:
        return BUCKET_ORDER[0]
    if maturity <= CUTOFFS['2019-10-31']:
        return BUCKET_ORDER[1]
    if maturity <= CUTOFFS['2019-12-31']:
        return BUCKET_ORDER[2]
    if maturity <= CUTOFFS['2020-06-30']:
        return BUCKET_ORDER[3]
    return BUCKET_ORDER[4]


def new_view():
    return {
        'total': metric(),
        'matured_by_cutoff': collections.defaultdict(metric),
        'maturity_buckets': collections.defaultdict(metric),
        'by_start_year': collections.defaultdict(metric),
        'matured_by_2019_10_31_by_start_year': collections.defaultdict(metric),
        'matured_by_2019_10_31_by_term': collections.defaultdict(metric),
        'matured_by_2019_10_31_by_publication_year': collections.defaultdict(metric),
    }


def add_record(view, record, count=1):
    start = record['start']
    maturity = record['maturity']
    term = record['term']
    published = record['published']
    cents = record['cents']
    add_metric(view['total'], cents, count)
    add_metric(view['by_start_year'][start.year], cents, count)
    for label, cutoff in CUTOFFS.items():
        if maturity <= cutoff:
            add_metric(view['matured_by_cutoff'][label], cents, count)
    add_metric(view['maturity_buckets'][bucket_for(maturity)], cents, count)
    if maturity <= CUTOFFS['2019-10-31']:
        add_metric(view['matured_by_2019_10_31_by_start_year'][start.year], cents, count)
        add_metric(view['matured_by_2019_10_31_by_term'][term], cents, count)
        add_metric(view['matured_by_2019_10_31_by_publication_year'][published.year], cents, count)


def normalize(view):
    total = view['total']
    out = {'total': total, 'matured_by_cutoff': {}}
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
            'bucket': label, **v,
            'record_pct_of_total': pct(v['records'], total['records']),
            'known_amount_pct_of_total': pct(v['known_amount_cents'], total['known_amount_cents']),
        })
    for field, key_name in [
        ('by_start_year', 'year'),
        ('matured_by_2019_10_31_by_start_year', 'year'),
        ('matured_by_2019_10_31_by_term', 'term_months'),
        ('matured_by_2019_10_31_by_publication_year', 'publication_year'),
    ]:
        out[field] = [{key_name: k, **v} for k, v in sorted(view[field].items())]
    return out


def quantile(sorted_values, q):
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return round(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac, 2)


def build(root):
    paths = sorted((root / 'tables').glob('batch-[0-9][0-9][0-9][0-9].csv'))
    if len(paths) != 295:
        raise SystemExit(f'expected 295 batch tables, found {len(paths)}')

    source_after_same_batch_filter = metric()
    parse_failure_metric = metric()
    parse_failure_by_batch = collections.defaultdict(metric)
    parse_failure_examples = []
    parseable = []
    low_confidence_parseable = 0
    strict_groups = {}

    for path in paths:
        with path.open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if row.get('同批重复原图记录') == 'True':
                    continue
                cents = int(row['金额_分']) if row.get('金额_分') else None
                add_metric(source_after_same_batch_filter, cents)
                batch = int(row['批次'])
                try:
                    start = dt.date.fromisoformat(row['借款开始日期'])
                    term = int(row['借款期限_月'])
                    published = dt.date.fromisoformat(row['公示发布日期'])
                    if term <= 0:
                        raise ValueError('non-positive term')
                    maturity = add_months(start, term)
                except Exception as exc:
                    add_metric(parse_failure_metric, cents)
                    add_metric(parse_failure_by_batch[batch], cents)
                    if len(parse_failure_examples) < 20:
                        parse_failure_examples.append({
                            'batch': batch,
                            'serial': row.get('序号'),
                            'loan_start': row.get('借款开始日期'),
                            'term_months': row.get('借款期限_月'),
                            'missing_fields': row.get('原表未提供字段', ''),
                            'error': str(exc),
                        })
                    continue

                hc = maturity_fields_high_confidence(row)
                if not hc:
                    low_confidence_parseable += 1
                record = {
                    'batch': batch,
                    'start': start,
                    'term': term,
                    'maturity': maturity,
                    'published': published,
                    'cents': cents,
                    'high_confidence': hc,
                }
                parseable.append(record)

                key = strict_key(row, cents)
                group = strict_groups.get(key)
                if group is None:
                    group = {'record': record, 'by_batch': collections.Counter()}
                    strict_groups[key] = group
                group['by_batch'][batch] += 1

    all_view = new_view()
    high_conf_view = new_view()
    all_lags = []
    node_lags = []
    for record in parseable:
        add_record(all_view, record)
        if record['high_confidence']:
            add_record(high_conf_view, record)
        lag = (record['published'] - record['maturity']).days
        all_lags.append(lag)
        if record['maturity'] <= CUTOFFS['2019-10-31']:
            node_lags.append(lag)

    conservative_view = new_view()
    conservative_hc_view = new_view()
    repeat_stats = {
        'multi_batch_keys': 0,
        'records_in_multi_batch_keys': 0,
        'removed_cross_batch_occurrences': 0,
        'removed_known_amount_cents': 0,
        'removed_unknown_amount_occurrences': 0,
        'max_distinct_batches_for_one_key': 0,
        'max_total_occurrences_for_one_key': 0,
    }
    repeat_examples = []

    for key, group in strict_groups.items():
        record = group['record']
        counts = group['by_batch']
        total_count = sum(counts.values())
        keep_count = max(counts.values())
        add_record(conservative_view, record, keep_count)
        if record['high_confidence']:
            add_record(conservative_hc_view, record, keep_count)
        if len(counts) > 1:
            removed = total_count - keep_count
            repeat_stats['multi_batch_keys'] += 1
            repeat_stats['records_in_multi_batch_keys'] += total_count
            repeat_stats['removed_cross_batch_occurrences'] += removed
            if record['cents'] is None:
                repeat_stats['removed_unknown_amount_occurrences'] += removed
            else:
                repeat_stats['removed_known_amount_cents'] += removed * record['cents']
            repeat_stats['max_distinct_batches_for_one_key'] = max(
                repeat_stats['max_distinct_batches_for_one_key'], len(counts))
            repeat_stats['max_total_occurrences_for_one_key'] = max(
                repeat_stats['max_total_occurrences_for_one_key'], total_count)
            if len(repeat_examples) < 20:
                repeat_examples.append({
                    'masked_name': key[0], 'masked_id': key[1], 'masked_phone': key[2],
                    'loan_start': key[3], 'term_months': key[4], 'amount_cents': key[5],
                    'batch_counts': dict(sorted(counts.items())),
                    'total_occurrences': total_count, 'kept_occurrences': keep_count,
                    'removed_cross_batch_occurrences': removed,
                    'computed_maturity': record['maturity'].isoformat(),
                })

    all_norm = normalize(all_view)
    hc_norm = normalize(high_conf_view)
    cons_norm = normalize(conservative_view)
    cons_hc_norm = normalize(conservative_hc_view)

    all_lags.sort()
    node_lags.sort()
    lag_stats = {
        'all_parseable_records': {
            'min_days': all_lags[0],
            'p25_days': quantile(all_lags, 0.25),
            'median_days': quantile(all_lags, 0.50),
            'p75_days': quantile(all_lags, 0.75),
            'max_days': all_lags[-1],
            'published_before_final_contract_maturity_records': sum(1 for x in all_lags if x < 0),
        },
        'matured_by_2019_10_31_subset': {
            'min_days': node_lags[0],
            'p25_days': quantile(node_lags, 0.25),
            'median_days': quantile(node_lags, 0.50),
            'p75_days': quantile(node_lags, 0.75),
            'max_days': node_lags[-1],
        },
    }

    result = {
        'generated_at': dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds'),
        'method': {
            'maturity_date': '借款开始日期 + 借款期限（月），按自然月相加；目标月份无对应日时取月末',
            'same_batch_duplicates': '排除 同批重复原图记录=True，与现有 statistics/summary.json 一致',
            'strict_fingerprint': '掩码姓名+掩码身份证/统一信用代码+掩码手机号+借款开始日期+借款期限+金额；仅做跨批重复敏感性分析',
            'conservative_cross_batch_dedup': '每个严格指纹保留任一单批最大出现次数，仅移除跨批额外出现部分；不宣称得到唯一债权数',
        },
        'source': {
            'batch_count': len(paths),
            'records_after_same_batch_duplicate_filter': source_after_same_batch_filter,
            'maturity_parse_failures': parse_failure_metric,
            'parse_failure_by_batch': [{'batch': k, **v} for k, v in sorted(parse_failure_by_batch.items())],
            'parse_failure_examples': parse_failure_examples,
            'parseable_records': all_norm['total'],
            'low_confidence_parseable_records': low_confidence_parseable,
        },
        'all_parseable_records_view': all_norm,
        'high_confidence_records_view': hc_norm,
        'conservative_strict_fingerprint_view': cons_norm,
        'conservative_strict_fingerprint_high_confidence_view': cons_hc_norm,
        'cross_batch_exact_fingerprint_sensitivity': {
            **repeat_stats,
            'removed_amount_pct_of_parseable_known_amount': pct(
                repeat_stats['removed_known_amount_cents'], all_norm['total']['known_amount_cents']),
            'examples': repeat_examples,
        },
        'maturity_to_publication_lag': lag_stats,
    }

    out = root / 'statistics'
    out.mkdir(exist_ok=True)
    (out / 'maturity-analysis.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    with (out / 'maturity-analysis.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['视图', '到期区间', '记录数', '已知逾期金额_元', '记录占比_%', '金额占比_%'])
        for name, view in [
            ('可计算到期日的全部记录', all_norm),
            ('高置信度到期字段', hc_norm),
            ('保守严格指纹跨批去重敏感性', cons_norm),
            ('保守严格指纹跨批去重+高置信度', cons_hc_norm),
        ]:
            for row in view['maturity_buckets']:
                w.writerow([name, row['bucket'], row['records'], money(row['known_amount_cents']),
                            row['record_pct_of_total'], row['known_amount_pct_of_total']])

    with (out / 'maturity-by-start-year.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['借款开始年份', '可计算记录数', '可计算已知金额_元',
                    '截至2019-10-31已合同到期记录数', '截至2019-10-31已合同到期金额_元'])
        totals = {x['year']: x for x in all_norm['by_start_year']}
        matured = {x['year']: x for x in all_norm['matured_by_2019_10_31_by_start_year']}
        for year in sorted(totals):
            t, m = totals[year], matured.get(year, metric())
            w.writerow([year, t['records'], money(t['known_amount_cents']),
                        m['records'], money(m['known_amount_cents'])])

    node = all_norm['matured_by_cutoff']['2019-10-31']
    node_cons = cons_norm['matured_by_cutoff']['2019-10-31']
    end2018 = all_norm['matured_by_cutoff']['2018-12-31']
    source = source_after_same_batch_filter
    failures = parse_failure_metric
    pub_rows = all_norm['matured_by_2019_10_31_by_publication_year']

    lines = [
        '# 严重失信公示贷款到期时间分析', '',
        f'> 生成时间：{result["generated_at"]}  ',
        '> 目的：统计后来进入严重失信公示的贷款中，有多少在有利网 2019-10-31 仍公开“逾期为 0”的节点之前，已按公示中的“借款开始日期 + 借款期限”达到最终合同到期日。', '',
        '## 核心结果', '',
        f'- 与现有总统计一致，排除同批重复原图后共有 **{source["records"]:,}** 条公示记录、可读逾期金额按公示人次累计 **{yi(source["known_amount_cents"])} 亿元**。',
        f'- 其中 **{failures["records"]:,}** 条无法计算最终合同到期日，对应 **{yi(failures["known_amount_cents"])} 亿元**；目前全部来自第 214 批，原表明确未提供“借款期限”。因此可计算到期日的为 **{all_norm["total"]["records"]:,}** 条。',
        f'- 在可计算记录中，最终合同到期日 **不晚于 2019-10-31** 的有 **{node["records"]:,}** 条，占 **{node["record_pct_of_total"]:.2f}%**；对应按公示人次累计可读逾期金额 **{yi(node["known_amount_cents"])} 亿元**，占可计算金额 **{node["known_amount_pct_of_total"]:.2f}%**。',
        f'- 更早一层：最终合同到期日 **不晚于 2018-12-31** 的已有 **{end2018["records"]:,}** 条、**{yi(end2018["known_amount_cents"])} 亿元**。也就是说，这批贷款在“2019-10-31 零逾期”节点前至少已经过了约 10 个月。',
        f'- 采用“保守严格指纹跨批去重”敏感性视图后，2019-10-31 前已最终到期的仍有 **{node_cons["records"]:,}** 条、**{yi(node_cons["known_amount_cents"])} 亿元**。', '',
        '## 为什么这比单看放款年份更强', '',
        '“2017 年放款”本身不能证明 2019 年已经违约；但如果一笔贷款的公示字段显示其 **最终合同到期日已经在 2019-10-31 之前**，而它后来又进入严重失信公示，那么至少可以确认：这不是一笔在 2019-10-31 时仍因原始合同期限尚未届满而无法判断结果的贷款。', '',
        '这仍不能单独证明平台 2019-10-31 已把这些贷款全部认定为坏账，也不能排除展期、重组、第三方代偿等情形。真正需要追问的是：**这些已过最终合同到期日、后来又进入严重失信公示的贷款，当时借款人实际是否已违约；如果出借人端仍显示“逾期为 0”，是否是由担保/代偿机制吸收；平台内部掌握的底层真实逾期和代偿压力是多少。**', '',
        '## 按放款年份看：2019-10-31 前已经最终到期', '',
        '| 放款年份 | 已到期记录数 | 按公示人次累计金额（亿元） |',
        '| --- | ---: | ---: |',
    ]
    for row in all_norm['matured_by_2019_10_31_by_start_year']:
        lines.append(f'| {row["year"]} | {row["records"]:,} | {yi(row["known_amount_cents"])} |')

    lines.extend(['', '## 这些老到期贷款后来何时才进入严重失信公示', '',
                  '| 公示年份 | 记录数 | 按公示人次累计金额（亿元） |',
                  '| --- | ---: | ---: |'])
    for row in pub_rows:
        lines.append(f'| {row["publication_year"]} | {row["records"]:,} | {yi(row["known_amount_cents"])} |')

    lines.extend(['', '## 跨批重复敏感性', '',
        f'- 完全相同严格指纹出现在多个批次的组合：**{repeat_stats["multi_batch_keys"]:,}** 个。',
        f'- 按“每个指纹保留任一单批最大出现次数”的保守方法，只移除跨批额外出现 **{repeat_stats["removed_cross_batch_occurrences"]:,}** 条，对应 **{yi(repeat_stats["removed_known_amount_cents"])} 亿元**，约占可计算公示人次累计金额的 **{result["cross_batch_exact_fingerprint_sensitivity"]["removed_amount_pct_of_parseable_known_amount"]:.2f}%**。',
        '- 这说明“完全相同字段的跨批重复”确实存在，但在金额上不是 97.94 亿元口径的主要部分。仍需注意：同一债权若后续金额发生变化，不会被该严格指纹识别；而掩码身份也可能造成偶然碰撞。因此这只是敏感性分析，不能替代唯一债权 ID 去重。', '',
        '## 到期区间（可计算到期日的全部记录）', '',
        '| 到期区间 | 记录数 | 金额（亿元） | 金额占比 |',
        '| --- | ---: | ---: | ---: |'])
    for row in all_norm['maturity_buckets']:
        lines.append(f'| {row["bucket"]} | {row["records"]:,} | {yi(row["known_amount_cents"])} | {row["known_amount_pct_of_total"]:.2f}% |')

    lag = lag_stats['matured_by_2019_10_31_subset']
    lines.extend(['', '## 到期日至后来公示日的时间间隔', '',
        f'- 对“2019-10-31 前已最终到期”的记录，最终合同到期日至严重失信公示日的间隔中位数为 **{lag["median_days"]:.0f} 天**，四分位区间约 **{lag["p25_days"]:.0f}—{lag["p75_days"]:.0f} 天**。',
        '- 这个时间间隔只能说明“最终到期日 → 后来被公开列入严重失信公示”的时间差，**不能当作平台首次知道违约的时间**；平台内部识别风险的时间可能早得多。', '',
        '## 口径边界', '',
        '- 这里计算的是“最终合同到期日”，不是分期贷款每一期应还款日，因此对判断 2019 年以前已发生的分期逾期反而是偏保守的。',
        '- 第 214 批 2,000 条记录原表未提供借款期限，未纳入到期日统计。',
        '- 金额仍是公示记录按人次累计；本文的严格指纹视图只用于测试完全相同跨批记录对结论的影响，不宣称得到唯一债权本金。',
        ''])
    (out / 'maturity-analysis.md').write_text('\n'.join(lines), encoding='utf-8')

    print(json.dumps({
        'source_records': source['records'],
        'parse_failure_records': failures['records'],
        'parseable_records': all_norm['total']['records'],
        'matured_by_2019_10_31': node,
        'matured_by_2018_12_31': end2018,
        'conservative_dedup_matured_by_2019_10_31': node_cons,
        'cross_batch_repeat_sensitivity': repeat_stats,
        'lag': lag_stats,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    build(args.root.resolve())
