#!/usr/bin/env python3
"""Build evidence-backed X trading reports and stage their README entries."""

import argparse
import csv
import html
import io
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


TRADE_WORDS = re.compile(
    r"买入|卖出|做多|做空|平仓|平掉|止盈|止损|建仓|加仓|减仓|开仓|清仓|爆仓|亏|赚|持仓|"
    r"\b(buy|bought|sell|sold|long|short|entry|exit|closed|position|profit|loss|stop|tp|sl)\b",
    re.IGNORECASE,
)
TABLE_START = "<!-- trader-table:start -->"
TABLE_END = "<!-- trader-table:end -->"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def account_handle(value):
    value = value.strip()
    if value.startswith(("https://", "http://")):
        parsed = urlsplit(value)
        require(parsed.hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
                "账号链接必须来自 x.com 或 twitter.com")
        value = parsed.path.strip("/").split("/")[0]
    value = value.removeprefix("@").lower()
    require(re.fullmatch(r"[a-z0-9_]{1,15}", value), "无效的 X 账号")
    return value


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, f"时间必须带时区 {value}")
    return result


def public_url(value):
    parsed = urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username,
            f"来源须为公开 HTTPS URL {value}")
    return value


def finite_price(value):
    require(isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0, f"价格必须是有限正数 {value}")
    return value


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def initialize(args):
    handle = account_handle(args.account)
    folder = Path(args.workdir)
    require(not folder.exists() or not any(folder.iterdir()), "工作目录必须为空，避免覆盖已采集证据")
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "manifest.json", {
        "account": handle, "displayName": handle, "profile": f"https://x.com/{handle}",
        "displayTimeZone": "Asia/Taipei", "collectedAt": None,
        "method": "", "limitations": [], "completeArchive": False,
        "collectionStatus": "pending", "pairingReviewed": False,
    })
    for filename, value in (("posts.json", []), ("trades.json", []), ("market-prices.json", {})):
        write_json(folder / filename, value)
    print(folder.resolve())


def normalize_posts(raw_posts, handle):
    posts = {}
    for raw in raw_posts:
        parsed = urlsplit(public_url(raw["url"]))
        match = re.fullmatch(r"/([A-Za-z0-9_]+)/status/(\d+)/?", parsed.path)
        require(parsed.hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
                and match and match[1].lower() == handle, "posts.json 只接受目标账号本人的状态链接")
        post_id = match[2]
        observed_at = timestamp(raw["datetime"])
        body = raw.get("body", raw.get("text", ""))
        require(isinstance(body, str) and body.strip(), f"帖子 {post_id} 缺少原文")
        post = {
            "url": f"https://x.com/{handle}/status/{post_id}",
            "datetime": observed_at.astimezone(timezone.utc).isoformat(),
            "body": body, "text": raw.get("text", body),
            "truncated": bool(raw.get("truncated", False)),
        }
        require(isinstance(post["text"], str), f"帖子 {post_id} 的 text 必须是字符串")
        if post_id in posts:
            require(posts[post_id]["datetime"] == post["datetime"], f"帖子 {post_id} 时间冲突")
            previous = posts[post_id]
            if (not previous["truncated"], len(previous["body"])) >= (not post["truncated"], len(body)):
                continue
        posts[post_id] = post
    return posts


def normalize_market(raw_market):
    result = {}
    for ticker, raw in raw_market.items():
        if raw.get("error"):
            result[ticker] = {"error": str(raw["error"])}
            continue
        market = dict(raw)
        require(market.get("source") and market.get("currency"), f"{ticker} 缺少来源或币种")
        public_url(market["page"])
        ZoneInfo(market["timeZone"])
        prices = {}
        for quote in market["prices"]:
            day = date.fromisoformat(quote["date"]).isoformat()
            price = finite_price(quote["close"])
            require(day not in prices or prices[day] == price, f"{ticker} {day} 收盘价冲突")
            prices[day] = price
        market["prices"] = [{"date": day, "close": prices[day]} for day in sorted(prices)]
        for requested, previous in market.get("previousSessions", {}).items():
            require(date.fromisoformat(previous) < date.fromisoformat(requested)
                    and previous in prices, f"{ticker} 上一交易日映射无效")
        result[ticker] = market
    return result


def closing(market, evidence_time):
    if not evidence_time or not market or market.get("error"):
        return None
    requested = timestamp(evidence_time).astimezone(ZoneInfo(market["timeZone"])).date().isoformat()
    session = market.get("previousSessions", {}).get(requested, requested)
    prices = {quote["date"]: quote["close"] for quote in market["prices"]}
    if session not in prices:
        return None
    return {
        "date": session, "close": prices[session], "requestedDate": requested,
        "shifted": session != requested, "source": market["source"],
        "url": market["page"], "currency": market["currency"],
    }


def price_return(entry, exit_price, direction):
    if entry is None or exit_price is None or direction not in {"多", "空"}:
        return None
    sign = 1 if direction == "多" else -1
    return sign * (exit_price / entry - 1) * 100


def normalize_trades(raw_rows, posts, market):
    rows, used_ids, paired = [], set(), set()
    for raw in raw_rows:
        row = dict(raw)
        row_id = row["id"]
        require(re.fullmatch(r"T\d{3,}", row_id) and row_id not in used_ids, "交易编号须唯一且形如 T001")
        used_ids.add(row_id)
        require(row.get("symbol") and row.get("notes") and row.get("currency"), f"{row_id} 缺少标的、备注或币种")
        require(row["direction"] in {"多", "空", "混合"}, f"{row_id} 方向无效")
        require(row["grade"] in {"A", "B", "G", "U"}, f"{row_id} 证据类型无效")
        require(row["result"] in {"赢", "亏", "平", "未明", "未平"}, f"{row_id} 结果无效")
        for side in ("entry", "exit", "refs", "outcomeRefs"):
            values = row.get(side, [])
            require(isinstance(values, list) and all(isinstance(item, str) and item in posts for item in values),
                    f"{row_id} 的 {side} 引用了不存在的帖子")
            row[side] = list(dict.fromkeys(values))
        row["refs"] = list(dict.fromkeys(row["entry"] + row["exit"] + row["refs"] + row["outcomeRefs"]))
        require(row["refs"], f"{row_id} 缺少证据")
        for side in ("entry", "exit"):
            row[side + "Time"] = posts[row[side][0]]["datetime"] if row[side] else None
            if row.get(side + "Price") is not None:
                finite_price(row[side + "Price"])
            else:
                row[side + "Price"] = None
        if row["grade"] == "A":
            require(row["entry"] and row["exit"] and row["direction"] in {"多", "空"}
                    and row["result"] in {"赢", "亏", "平"} and row["outcomeRefs"]
                    and row.get("outcomeBasis") in {"statement", "quoted_prices"},
                    f"{row_id} 可配对样本缺少完整入场、退出、结果依据")
            require(timestamp(row["entryTime"]) <= timestamp(row["exitTime"]), f"{row_id} 退出早于入场证据")
            key = (row["symbol"], row["direction"], tuple(sorted(row["entry"])), tuple(sorted(row["exit"])))
            require(key not in paired, f"{row_id} 重复持仓段")
            paired.add(key)
        row.setdefault("cash", "未披露 / 不可拆分")
        row.setdefault("priceMode", "reference")
        require(row["priceMode"] in {"paired", "reference", "conflict", "underlying"}, f"{row_id} priceMode 无效")
        row.setdefault("period", "")
        row.setdefault("ticker", None)
        quotes = market.get(row["ticker"])
        require(not quotes or quotes.get("error") or quotes["currency"] == row["currency"], f"{row_id} 行情与原文币种不同")
        row["entryClose"] = closing(quotes, row["entryTime"])
        row["exitClose"] = closing(quotes, row["exitTime"])
        row["returnPct"] = None
        row["filledReturnPct"] = None
        row["closeReturnPct"] = None
        if row["priceMode"] != "conflict" and (row.get("pricesComparable") is True or row.get("proxyEligible") is True):
            row["returnPct"] = price_return(row["entryPrice"], row["exitPrice"], row["direction"])
        if row.get("outcomeBasis") == "quoted_prices":
            value = row["returnPct"]
            require(value is not None and row["result"] == ("赢" if value > 0 else "亏" if value < 0 else "平"),
                    f"{row_id} 原文价格与结果不符，请核对或改用明确自述依据")
        row["proxyNote"] = "证据或可比价格不足，收益估算留空。"
        if row["grade"] != "A":
            row["proxyNote"] = "复盘、组合或未确认条目，发帖日行情仅供对照。"
        elif row["priceMode"] == "conflict":
            row["proxyNote"] = "原文价格存在冲突，须核对实际报价。"
        elif row.get("proxyEligible") is True and row["priceMode"] != "underlying":
            entry, exit_quote = row["entryClose"], row["exitClose"]
            row["filledReturnPct"] = row["returnPct"]
            if entry and exit_quote and entry["date"] != exit_quote["date"]:
                row["closeReturnPct"] = price_return(entry["close"], exit_quote["close"], row["direction"])
            market_zone = ZoneInfo(quotes["timeZone"]) if quotes and not quotes.get("error") else timezone.utc
            same_day = (timestamp(row["entryTime"]).astimezone(market_zone).date()
                        == timestamp(row["exitTime"]).astimezone(market_zone).date())
            same_day = same_day or bool(entry and exit_quote and entry["date"] == exit_quote["date"])
            if row["returnPct"] is None and not same_day:
                entry_price = row["entryPrice"] if row["entryPrice"] is not None else entry and entry["close"]
                exit_price = row["exitPrice"] if row["exitPrice"] is not None else exit_quote and exit_quote["close"]
                row["filledReturnPct"] = price_return(entry_price, exit_price, row["direction"])
            if row["filledReturnPct"] is not None:
                row["proxyNote"] = "原文报价优先，缺失端取证据对应日收盘。价格参考未计费用及未披露的仓位变化。"
            elif same_day:
                row["proxyNote"] = "同日买卖且报价不全，日线无法还原此笔收益。"
        rows.append(row)
    return rows


def csv_text(rows):
    stream = io.StringIO(newline="")
    fields = ["id", "symbol", "direction", "result", "currency", "entryTime", "exitTime",
              "entryPrice", "exitPrice", "returnPct", "filledReturnPct", "cash", "notes", "refs"]
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        values = {key: row.get(key) for key in fields}
        values["refs"] = " ".join(row["refs"])
        for key, value in values.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                values[key] = "'" + value
        writer.writerow(values)
    return stream.getvalue()


def build(args):
    folder, output = Path(args.workdir), Path(args.output)
    manifest = read_json(folder / "manifest.json")
    handle = account_handle(manifest["account"])
    require(manifest.get("collectionStatus") in {"complete", "partial"}, "先记录实际采集状态 complete 或 partial")
    require(manifest.get("pairingReviewed") is True, "须完成买卖证据人工配对复核后再生成")
    require(manifest.get("method") and isinstance(manifest.get("limitations"), list), "须记录采集方法和限制")
    require(manifest.get("collectionStatus") != "partial" or manifest["limitations"], "部分采集须记录缺口")
    require(not manifest.get("completeArchive") or manifest.get("archiveCompletenessEvidence"), "声称完整历史须提供覆盖核对依据")
    zone = ZoneInfo(manifest.get("displayTimeZone", "Asia/Taipei"))
    collected = timestamp(manifest["collectedAt"])
    posts = normalize_posts(read_json(folder / "posts.json"), handle)
    market = normalize_market(read_json(folder / "market-prices.json"))
    rows = normalize_trades(read_json(folder / "trades.json"), posts, market)
    require(all(timestamp(post["datetime"]) <= collected for post in posts.values()), "采集时间早于帖子时间")
    samples = [row for row in rows if row["grade"] == "A"]
    wins = sum(row["result"] == "赢" for row in samples)
    losses = sum(row["result"] == "亏" for row in samples)
    flats = sum(row["result"] == "平" for row in samples)
    rate = 100 * wins / (wins + losses) if wins + losses else None
    candidate_ids = [post_id for post_id, post in posts.items() if TRADE_WORDS.search(post["body"])]
    candidate_ids = list(dict.fromkeys(candidate_ids + manifest.get("candidateIds", [])))
    require(all(item in posts for item in candidate_ids), "候选列表引用不存在的帖子")
    truncated = sum(post["truncated"] or "Show more" in post["text"] for post in posts.values())
    times = sorted(post["datetime"] for post in posts.values())
    manifest.update(account=handle, profile=f"https://x.com/{handle}", displayTimeZone=str(zone),
                    postCount=len(posts), candidateCount=len(candidate_ids), stillTruncated=truncated,
                    ledgerCount=len(rows), aCount=len(samples), wins=wins, losses=losses, flats=flats,
                    winRate=rate, generatedAt=datetime.now(timezone.utc).isoformat(),
                    earliest=times[0] if times else None, latest=times[-1] if times else None)
    display_name = manifest.get("displayName") or handle
    snapshot = collected.astimezone(zone).date().isoformat()
    coverage = f"已保存 {len(posts):,} 条公开记录，配对样本 {len(samples)} 段。"
    if times:
        coverage += f"原文覆盖 {timestamp(times[0]).astimezone(zone).date()} 至 {timestamp(times[-1]).astimezone(zone).date()}。"
    data = {"rows": rows, "posts": list(posts.values()), "manifest": manifest, "market": market,
            "csv": csv_text(rows), "candidateIds": candidate_ids}
    page = (Path(__file__).parent / "report-template.html").read_text(encoding="utf-8")
    substitutions = {
        "TITLE": display_name, "HANDLE": handle, "SNAPSHOT": snapshot, "COVERAGE": coverage,
        "POST_COUNT": f"{len(posts):,}", "CANDIDATE_COUNT": len(candidate_ids),
        "TRUNCATED_COUNT": truncated, "TIMEZONE": str(zone), "COLLECTION_METHOD": manifest["method"],
    }
    substitutions = {key: html.escape(str(value), quote=True) for key, value in substitutions.items()}
    limitations = "".join(f"<li>{html.escape(str(item))}</li>" for item in manifest["limitations"])
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    encoded = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    substitutions.update(LIMITATIONS=limitations, DATA=encoded)
    page = re.sub(r"__([A-Z_]+)__", lambda match: substitutions[match[1]], page)
    summary = {"account": handle, "displayName": display_name, "sampleCount": len(samples),
               "wins": wins, "losses": losses, "flats": flats, "winRate": rate, "snapshot": snapshot}
    write_text(output / "index.html", page)
    write_json(output / "summary.json", summary)
    write_json(output / "manifest.json", manifest)
    write_json(output / "trades.json", rows)
    write_text(output / "trades.csv", "\ufeff" + data["csv"])
    print(json.dumps(summary, ensure_ascii=False))


def markdown_text(value):
    return html.escape(str(value), quote=True).replace("|", "&#124;").replace("[", "&#91;").replace("]", "&#93;").replace("\n", " ")


def stage(args):
    report, repo = Path(args.report), Path(args.repo)
    summary = read_json(report / "summary.json")
    handle = account_handle(summary["account"])
    page = (report / "index.html").read_text(encoding="utf-8")
    payload = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.DOTALL)
    require(payload, "报告缺少内嵌数据")
    data = json.loads(payload[1])
    require(data["manifest"]["account"] == handle, "报告与摘要账号不匹配")
    expected = (summary["sampleCount"], summary["wins"], summary["losses"], summary["flats"], summary["winRate"])
    actual = tuple(data["manifest"][key] for key in ("aCount", "wins", "losses", "flats", "winRate"))
    require(expected == actual, "报告与 README 统计摘要不一致，请重新 build")
    readme_path = repo / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    require(readme.count(TABLE_START) == 1 and readme.count(TABLE_END) == 1, "README 须有唯一 trader-table 标记")
    start = readme.index(TABLE_START) + len(TABLE_START)
    end = readme.index(TABLE_END)
    require(start < end, "README 表格标记顺序错误")
    old_rows = [line for line in readme[start:end].splitlines() if line.startswith("|") and "https://x.com/" in line]
    own_profile = re.compile(r"https://x\.com/" + re.escape(handle) + r"(?:[)/?#]|$)", re.IGNORECASE)
    other_rows = [line for line in old_rows if not own_profile.search(line)]
    sample = f"{summary['sampleCount']} 段（{summary['wins']} 赢、{summary['losses']} 亏、{summary['flats']} 平）"
    win_rate = f"{summary['winRate']:.1f}%" if summary["winRate"] is not None else "暂无可计算样本"
    report_path = f"reports/{handle}/index.html"
    report_link = f"./{report_path}"
    if args.site_url:
        report_link = public_url(args.site_url.rstrip("/") + "/" + report_path)
    new_row = f"| {markdown_text(summary['displayName'])} | [X · @{handle}](https://x.com/{handle}) | {sample} | **{win_rate}** | [查看报告]({report_link}) |"
    table = "\n| 博主 | 平台 | 配对样本 | 样本胜率 | 报告 |\n| --- | --- | ---: | ---: | --- |\n"
    table += "\n".join(other_rows + [new_row]) + "\n"
    updated_readme = readme[:start] + table + readme[end:]
    destination = repo / "reports" / handle
    require(not destination.is_symlink() and destination.resolve().is_relative_to(repo.resolve()), "报告目录超出仓库")
    write_text(destination / "index.html", page)
    write_json(destination / "summary.json", summary)
    write_text(readme_path, updated_readme)
    print(f"reports/{handle}/index.html\nreports/{handle}/summary.json\nREADME.md")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="创建待采集的工作目录")
    init.add_argument("--account", required=True)
    init.add_argument("--workdir", required=True)
    init.set_defaults(run=initialize)
    renderer = commands.add_parser("build", help="验证证据数据并生成离线 HTML")
    renderer.add_argument("--workdir", required=True)
    renderer.add_argument("--output", required=True)
    renderer.set_defaults(run=build)
    publisher = commands.add_parser("stage", help="放置账号报告并更新 README，不执行 git 或联网")
    publisher.add_argument("--report", required=True)
    publisher.add_argument("--repo", required=True)
    publisher.add_argument("--site-url")
    publisher.set_defaults(run=stage)
    args = parser.parse_args()
    try:
        args.run(args)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"错误：{error}\n")


if __name__ == "__main__":
    main()
