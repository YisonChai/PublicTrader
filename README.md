# PublicTrader

PublicTrader 统计社交平台上公开喊单的交易博主胜率。报告把每笔入场与退出的帖子配对，列出发帖时间、对应交易日收盘价、收益估算和原文证据。

## 雪球组合

[查看运行中雪球组合的收益曲线与量化雷达图](https://yisonchai.github.io/PublicTrader/xueqiu.html)。包含持仓、主理人信息、盈亏贡献与近一年调仓；[评分脚本](./scripts/score_xueqiu.py)可基于页面内的快照重算。

<!-- trader-table:start -->
| 博主 | 平台 | 配对样本 | 样本胜率 | 报告 |
| --- | --- | ---: | ---: | --- |
| 电鱼哥专注SpaceX | [X · @iiiinvest](https://x.com/iiiinvest) | 24 段（13 赢、10 亏、1 平） | **56.5%** | [HTML](https://yisonchai.github.io/PublicTrader/?v=fc188c7#ledger) |
<!-- trader-table:end -->



<img width="1320" height="729" alt="image" src="https://github.com/user-attachments/assets/81e7721d-b2ed-4eb8-a1ff-6f5e2b1169bf" />

## 输入 X 账号生成报告

仓库提供 [x-trader-audit skill](./skills/x-trader-audit/SKILL.md)，将公开记录采集、买卖证据配对、历史收盘价核对、HTML 生成和 GitHub 发布串成一套流程。

将 `skills/x-trader-audit` 目录复制到 `~/.codex/skills/`，在 Codex 中调用。

```text
$x-trader-audit https://x.com/账号
```

也可以指定日期范围或要求只生成本地报告。默认发布到本仓库，新增账号的网页地址为 `https://yisonchai.github.io/PublicTrader/reports/<账号>/`，README 表格按账号新增或更新一行。交易卡片采用中性灰毛玻璃、中文标签和鲜明收益配色，账本不分页。

Skill 通过可用的 X 数据工具、用户提供的导出或已登录浏览器取得公开记录，由 agent 逐笔审核证据，再运行 Python 3.10+ 脚本计算。它需要可用的数据访问方式和仓库写权限。受保护账号、删除帖子、未披露交易及加载失败会形成覆盖缺口，报告会记录实际范围。没有足够样本时显示“暂无可计算样本”。

已准备数据时，可直接使用随附脚本。输入格式见 [数据说明](./skills/x-trader-audit/schema.md)。

```bash
python3 skills/x-trader-audit/report.py init --account "@账号" --workdir /tmp/trader-audit
# 完成公开记录采集、证据复核及行情整理后执行
python3 skills/x-trader-audit/report.py build --workdir /tmp/trader-audit --output /tmp/trader-report
python3 skills/x-trader-audit/report.py stage --report /tmp/trader-report --repo . \
  --site-url https://yisonchai.github.io/PublicTrader
```

`stage` 将 HTML 和统计摘要放入对应账号目录，同时更新 README。随后由 skill 提交、推送并检查 GitHub Pages 部署结果。技能源码与模板在 `skills/x-trader-audit/`，公开网页部署来自 `main` 分支。
