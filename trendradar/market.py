# coding=utf-8
"""A股能源/粮食板块行情快照（新浪/腾讯公开接口，免 key）

标的采用板块 ETF + 龙头股代理，为 AI 分析提供实时涨跌与 30 日价格窗口，
使「新闻叙事」与「价格序列」可以互相验证。

接口说明：
- 实时行情：hq.sinajs.cn 批量接口（需 Referer）
- 日K历史：web.ifzq.gtimg.cn 前复权日K（行格式 [日期,开,收,高,低,量]，收盘价在下标 2）
- 弃用东财 clist/kline：批量翻页会触发其反爬封禁（RemoteDisconnected）
"""

import re
import socket
import time
from typing import Callable, Dict, List, Optional, Tuple

import requests

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn",
}

# (显示名, 行情代码)：首位为大盘参照
_SYMBOLS: List[Tuple[str, str]] = [
    ("上证指数", "sh000001"),
    # ── 能源 ──
    ("煤炭ETF", "sh515220"),
    ("石油ETF", "sh561360"),
    ("油气ETF", "sz159697"),
    ("电力ETF", "sz159611"),
    ("光伏ETF", "sh515790"),
    ("新能源车ETF", "sh515030"),
    # ── 粮食/农业 ──
    ("农业ETF", "sh516550"),
    ("养殖ETF", "sz159865"),
    ("隆平高科·种业", "sz000998"),
    ("云天化·磷肥", "sh600096"),
    ("牧原股份·生猪", "sz002714"),
]

_SINA_URL = "https://hq.sinajs.cn/list={symbols}"
_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """部分行情域名 AAAA 记录在 IPv6 路径会被重置，强制解析 A 记录"""
    try:
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    except socket.gaierror:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)


def _default_fetch(url: str, params: Optional[Dict] = None, timeout: int = 6,
                   retries: int = 2) -> requests.Response:
    """带重试的 GET（行情接口存在间歇性拒绝连接的窗口）"""
    last_err: Exception = OSError("unreachable")
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(2)
        try:
            # ponytail: socket.getaddrinfo 补丁非线程安全，引入并发前须换自定义 resolver
            socket.getaddrinfo = _ipv4_getaddrinfo
            r = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001 - 单次请求失败进入重试
            last_err = e
        finally:
            socket.getaddrinfo = _orig_getaddrinfo
    raise last_err


def _compute_stats(closes: List[float]) -> Optional[Dict]:
    """从按日升序的收盘价序列计算涨跌与区间位置（最后一根为最新价）"""
    if len(closes) < 2:
        return None
    last = closes[-1]

    def pct(n: int) -> Optional[float]:
        if len(closes) <= n or not closes[-1 - n]:
            return None
        return round((last / closes[-1 - n] - 1) * 100, 2)

    hi, lo = max(closes), min(closes)
    pos = round((last - lo) / (hi - lo) * 100) if hi > lo else 50
    return {"last": last, "chg5": pct(5), "chg20": pct(20), "hi": hi, "lo": lo, "pos30": pos}


def _fetch_realtime(fetch: Callable, symbols: List[str]) -> Dict[str, Tuple[str, float, float]]:
    """新浪实时批量行情：返回 {代码: (名称, 现价, 今日涨跌%)}，失败返回 {}"""
    try:
        r = fetch(_SINA_URL.format(symbols=",".join(symbols)))
        r.encoding = "gbk"
        out: Dict[str, Tuple[str, float, float]] = {}
        for m in re.finditer(r'var hq_str_(\w+)="([^"]*)"', r.text):
            sym, payload = m.group(1), m.group(2)
            fields = payload.split(",")
            if len(fields) < 4 or not fields[0]:
                continue
            try:
                cur, prev = float(fields[3]), float(fields[2])
                pct = round((cur / prev - 1) * 100, 2) if prev else 0.0
                out[sym] = (fields[0], cur, pct)
            except (ValueError, ZeroDivisionError):
                continue
        return out
    except Exception as e:
        print(f"[行情] 实时行情获取失败: {e}")
        return {}


def _fetch_closes(fetch: Callable, symbol: str, limit: int = 32) -> List[float]:
    """腾讯前复权日K收盘价序列（升序，最后一根为最新交易日）"""
    try:
        r = fetch(_TENCENT_KLINE_URL, params={"param": f"{symbol},day,,,{limit},qfq"})
        data = (r.json().get("data") or {}).get(symbol) or {}
        rows = data.get("qfqday") or data.get("day") or []
        closes: List[float] = []
        for row in rows:
            # 行格式：[日期, 开盘, 收盘, 最高, 最低, 成交量]
            if len(row) >= 3:
                try:
                    closes.append(float(row[2]))
                except (TypeError, ValueError):
                    continue
        return closes
    except Exception as e:
        print(f"[行情] {symbol} K线获取失败: {e}")
        return []


def _fmt_pct(v: Optional[float]) -> str:
    return "NA" if v is None else f"{v:+.2f}%"


def build_market_snapshot(
    now_str: str = "",
    fetch: Callable = _default_fetch,
    sleep: Callable = time.sleep,
) -> str:
    """构建注入 AI 提示词的行情快照文本

    任何失败均降级：单标的失败跳过，整体失败返回空串。
    """
    realtime = _fetch_realtime(fetch, [sym for _, sym in _SYMBOLS])

    lines: List[str] = []
    for i, (label, sym) in enumerate(_SYMBOLS):
        if i:
            if sleep:
                sleep(0.5)
        closes = _fetch_closes(fetch, sym)
        s = _compute_stats(closes) if closes else None
        rt = realtime.get(sym)

        if not s and not rt:
            continue

        if rt:
            spot = f"{rt[1]:g} 今日{_fmt_pct(rt[2])}"
        else:
            spot = f"{s['last']:g}"
        if s:
            stats_part = (f" | 5日{_fmt_pct(s['chg5'])} | 20日{_fmt_pct(s['chg20'])}"
                          f" | 30日区间{s['lo']:g}~{s['hi']:g} 位置{s['pos30']}%")
        else:
            stats_part = " | （无K线历史）"
        line = f"[{label} {sym}] {spot}{stats_part}"
        lines.append(f"大盘参照: {line}" if i == 0 else line)

    if not lines:
        return ""
    return f"A股能源/粮食板块行情（新浪/腾讯接口，截至 {now_str}）\n" + "\n".join(lines)
