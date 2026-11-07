# 输入与输出

四个输入文件使用 UTF-8 JSON。运行 `report.py init` 创建结构，再由 agent 填入真实采集和核对的数据。原始响应、截图与查询进度保留在工作目录，网页只内嵌规范化公开数据。

## manifest.json

必填 `account`、`displayName`、`collectedAt`（带时区 ISO 时间）、`displayTimeZone`（默认 Asia/Taipei）、`method`、`limitations`（字符串数组）、`collectionStatus`、`pairingReviewed`。

`collectionStatus` 为 `partial` 或 `complete`。采集未完成或存在缺口时写 `partial` 并给出具体限制。`completeArchive` 默认 false，设 true 时必须提供 `archiveCompletenessEvidence`。`pairingReviewed` 在完成语义配对审核后设 true。可用 `candidateIds` 补充词表未命中的候选状态 ID。条数、日期范围、胜率等由生成脚本重新计算。

## posts.json

数组，每项至少包含以下字段。

```json
{
  "url": "https://x.com/example/status/1234567890",
  "datetime": "2026-01-05T15:10:00Z",
  "body": "这里保存该作者的完整公开原文",
  "text": "可选的页面原始文本，含未展开标记时保留",
  "truncated": false
}
```

示例账号、ID 与正文只说明结构，不能当成真实证据。一个状态 ID 只保留一条，优先完整正文。URL 必须归属目标账号。别人的回复或引用放在单独上下文文件，只有目标作者明确认领的交易才可配对。

## trades.json

数组，每项为一个公开持仓段。价格缺失用 null，禁止使用 0 或编造数值。核心结构如下。

```json
{
  "id": "T001",
  "symbol": "已核实的标的名称",
  "direction": "多",
  "grade": "A",
  "result": "赢",
  "entry": ["入场状态ID"],
  "exit": ["退出状态ID"],
  "refs": ["辅助状态ID"],
  "outcomeRefs": ["明确结果状态ID"],
  "outcomeBasis": "statement",
  "notes": "写明为何归为同一持仓、是否加减仓、价格或日期的缺口",
  "entryPrice": null,
  "exitPrice": null,
  "currency": "USD",
  "ticker": "行情字典中已核实的代码",
  "priceMode": "reference",
  "pricesComparable": false,
  "proxyEligible": false,
  "cash": "未披露 / 不可拆分",
  "period": ""
}
```

- `id` 为唯一的 T001 格式编号，更新已有报告时维持稳定。
- `direction` 为 `多`、`空`、`混合`。后者不能直接套单标的价差公式。
- `grade` 为内部值。A 表示同一标的可核对的入场与实际退出且结果明确，B 表示复盘或链条不足，G 表示组合或汇总，U 表示结果未明或未平。网页不暴露字母。
- `result` 为 `赢`、`亏`、`平`、`未明`、`未平`。A 只允许前三种。主胜率根据这个自述或原文价格结果计算，与收盘价估算的正负分开。
- `entry` 和 `exit` 的第一个 ID 分别用于入场和退出证据日期，其余 ID 为同一持仓段的补充。`refs` 会自动合并全部证据。所有 ID 必须存在于 posts.json。
- A 必须有 `outcomeRefs` 与 `outcomeBasis`。`statement` 表示原文明确披露止盈、止损或结果，`quoted_prices` 表示两端明确可比的成交报价判断。后者需与多空方向和价差符号一致。
- `priceMode` 可为 `paired`（两端明确报价）、`reference`（初始仓或部分价格参考）、`conflict`（原文报价冲突）、`underlying`（只得到标的价格，无法还原策略收益）。
- `pricesComparable` 只有确认两端报价属于同一计价基础、币种、证券或合约且跨企业行动可比时才为 true。否则即使原文有两个数字，也不自动生成百分比。
- `proxyEligible` 只有完成证券身份、行情币种、日期、拆股及策略可比性核查后才为 true。它也表示已确认原文价格可比。仅 A 类型进入收盘补价估算。价格冲突、混合方向和 underlying 模式始终排除。
- `ticker` 为 null 时不会补行情。`currency` 为实际报价币种或 `未确定`，不同币种不能混算。`cash` 仅记录披露金额和口径，不用来倒推未知成交价，也不跨单求和。
- `entryTime`、`exitTime`、`returnPct`、`entryClose`、`exitClose`、`filledReturnPct`、`closeReturnPct`、`proxyNote` 均由脚本重算。输入中同名旧值不会被当作计算依据。

## market-prices.json

以核实过的代码为键。市场时区与币种必须来自数据源，价格必须为已完成交易日的真实未复权收盘。

```json
{
  "EXAMPLE": {
    "source": "实际行情来源名称",
    "page": "https://真实行情来源/具体标的/history",
    "currency": "USD",
    "timeZone": "America/New_York",
    "prices": [{"date": "2026-01-02", "close": 100.5}],
    "previousSessions": {"2026-01-03": "2026-01-02"}
  }
}
```

这是结构示例，数值不可用于真实报告。`previousSessions` 只填写已经由交易所日历确认的非交易日映射，并保留日历来源在采集记录。默认只匹配同日价格，缺行不猜上一交易日。跨长假也使用核实后的映射，不用固定四天窗口猜测。没有匹配行时显示暂无行情。

行情访问失败可用 `{"error":"具体原因"}` 保留该代码的缺口。每个实际报价保留原始响应或行情页面证据。对将进入统计的关键价位交叉核对另一来源，并记录差异原因。使用不同来源数据时统一未复权口径，拆股跨期不可比较的区间不自动计算收益。

## 输出与再运行

`build` 生成可离线打开的 HTML 和由同一数据计算的 summary。`stage` 校验摘要与内嵌统计一致，按账号更新 README 标记内一行，复制 HTML 和 summary 到 `reports/<handle>/`。其他文件保留在工作目录用于核查。

若重复运行同一账号，从旧 HTML 的 `script#data` JSON 中恢复 posts、rows、manifest、market。保留证据 ID 和已核对备注，补齐新增输入字段，再加入新记录重新计算。模板修改时只改当前 skill 的模板并重建要更新的账号，不能把一个账号的数据嵌进另一个账号页面。
