# 有利网年度公示统计

打开 `index.html` 查看离线统计页面，无需联网。按借款开始年份统计。

- `yearly-summary.csv`：每个借款年份的公示人次与已知逾期金额。
- `batch-summary.csv`：每批公示汇总。
- `batch-year-summary.csv`：每批按借款年份拆分。
- `summary.json`：供页面使用的汇总数据。
- `verification.json`：汇总一致性校验与原始 CSV 指纹。

排除同批重复原图的75条记录，保留跨批公示记录。由于身份脱敏，统计的是公示人次，不能认定为独立人数。金额使用整数分求和，未知金额不计入已知合计，也不计为零。第254批序号298、327两条未知金额均归属2017年。原文52个序号缺口未补造记录。借款年份不代表违约发生年份，公示覆盖2020-06-25至2024-10-29。

如更新源表，可运行：

```sh
python3 scripts/build_statistics.py --root /Volumes/250G/yooli_archive
```
