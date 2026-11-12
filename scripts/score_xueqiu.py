#!/usr/bin/env python3
"""Recalculate the four peer-relative scores in xueqiu.html.

Usage: python3 scripts/score_xueqiu.py [path/to/xueqiu.html]
The self-contained page contains its raw daily series, holdings and Notion event
snapshot. This program needs Python 3 only and does not fetch live data.
"""
import base64
import gzip
import json
import math
import re
import sys
import textwrap
from datetime import date
from pathlib import Path

PATH = Path(sys.argv[1] if len(sys.argv) > 1 else 'xueqiu.html')
PATTERN = r'(<script type="application/octet-stream" id="data">)(.*?)(</script>)'


def decode(encoded):
    raw = base64.b64decode(encoded)
    values, value, shift = [], 0, 0
    for b in raw:
        value |= (b & 127) << shift
        if b & 128:
            shift += 7
        else:
            values.append(value)
            value, shift = 0, 0
    if len(values) % 2 or shift:
        raise ValueError('invalid series encoding')
    day, pct, result = 0, 0, []
    for d, v in zip(values[::2], values[1::2]):
        day += d
        pct += v // 2 if v % 2 == 0 else -(v // 2) - 1
        result.append((day, pct / 100))
    return result


def metrics(r):
    pts = decode(r['series'][0]['data'])
    m = {'stability': None, 'profitability': None, 'diversity': None, 'frequency': None}
    if pts and all(1 + p / 100 > 0 for _, p in pts):
        peak, draw = 1.0, 0.0
        for _, p in pts:
            net = 1 + p / 100
            peak = max(peak, net)
            draw = max(draw, 1 - net / peak)
        m['stability'] = -draw * 100
        days = pts[-1][0] - pts[0][0]
        if days >= 365:
            m['profitability'] = ((1 + pts[-1][1] / 100) / (1 + pts[0][1] / 100)) ** (365.25 / days) * 100 - 100
    weights = [float(x['weight'].rstrip('%')) for x in r.get('holdings', []) if x.get('weight') and float(x['weight'].rstrip('%')) > 0]
    if weights:
        m['diversity'] = sum(weights) ** 2 / sum(w*w for w in weights)
    if r.get('eventsComplete'):
        events = set()
        for line in r.get('events', []):
            if '→' not in line or '已取消' in line:
                continue
            match = re.match(r'-\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\s+(\d\d:\d\d:\d\d)', line)
            if not match:
                continue
            y, month, day, timestamp = match.groups()
            when = date(int(y), int(month), int(day))
            if date(2025, 11, 12) <= when <= date(2026, 11, 12):
                events.add((when.isoformat(), timestamp))
        m['frequency'] = len(events)
    return m


def score(records):
    for r in records:
        r['metrics'] = metrics(r)
    for key in ('stability', 'profitability', 'diversity', 'frequency'):
        values = [r['metrics'][key] for r in records if r['metrics'][key] is not None and math.isfinite(r['metrics'][key])]
        for r in records:
            v = r['metrics'][key]
            if v is None or not math.isfinite(v) or not values:
                s = None
            elif len(values) == 1:
                s = None
            else:
                less = sum(x < v for x in values)
                ties = sum(x == v for x in values)
                s = round(5 * (less + (ties - 1) / 2) / (len(values) - 1), 2)
            r.setdefault('scores', {})[key] = s
    return records


def main():
    source = PATH.read_text(encoding='utf-8')
    found = re.search(PATTERN, source, flags=re.S)
    if not found:
        raise ValueError('embedded data not found')
    records = json.loads(gzip.decompress(base64.b64decode(found.group(2))))
    score(records)
    packed = base64.b64encode(gzip.compress(json.dumps(records, ensure_ascii=False, separators=(',', ':')).encode(), mtime=0)).decode()
    PATH.write_text(source[:found.start(2)] + '\n'.join(textwrap.wrap(packed, 100)) + source[found.end(2):], encoding='utf-8')
    print(f'Scored {len(records)} portfolios; missing dimensions: ' + ', '.join(f"{k}={sum(r['scores'][k] is None for r in records)}" for k in ('stability','profitability','diversity','frequency')))

if __name__ == '__main__':
    main()
