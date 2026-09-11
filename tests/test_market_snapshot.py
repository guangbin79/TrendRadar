# coding=utf-8
"""trendradar.market 行情快照模块自检

运行: uv run python tests/test_market_snapshot.py
覆盖: 统计计算纯函数 + 基于假 fetch 的快照构建 + 解析 + 全失败兜底
"""

from types import SimpleNamespace

from trendradar.market import _compute_stats, _fetch_realtime, build_market_snapshot


def test_compute_stats_basic():
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
              110, 111, 112, 113, 114, 115, 116, 117, 118, 119,
              120, 121, 124, 130]  # len=24, last=130
    s = _compute_stats(closes)
    assert s is not None
    assert s["last"] == 130
    # 5日前的收盘 = closes[-6] = 118 → (130/118-1)=10.17%
    assert s["chg5"] == 10.17, s
    # 20日前的收盘 = closes[-21] = 103 → 26.21%
    assert s["chg20"] == 26.21, s
    assert s["hi"] == 130 and s["lo"] == 100
    assert s["pos30"] == 100  # 收在最高点
    print("PASS: stats 基本计算")


def test_compute_stats_short_series():
    assert _compute_stats([5.0]) is None
    assert _compute_stats([]) is None
    s = _compute_stats([10.0, 10.5, 11.0])
    assert s["chg20"] is None and s["last"] == 11.0 and s["pos30"] == 100
    print("PASS: stats 边界（短序列）")


SINA_TEXT = (
    'var hq_str_sh000001="上证指数,3955.5489,3942.0879,3930.1164,3980.2022,3915.0";\n'
    'var hq_str_sh515220="煤炭ETF国泰,1.295,1.291,1.298,1.301,1.290";\n'
    'var hq_str_sz000998="隆平高科,10.00,9.90,10.20,10.30,9.85";\n'
    'var hq_str_szbad="";\n'  # 无效标的应被跳过
)

KLINE_JSON_TEMPLATE = {
    "code": 0, "data": {
        "sh000001": {"qfqday": [["2026-08-%02d" % (d + 1), "3000", str(3000 + d * 5), "0", "0", "0"] for d in range(24)]},
        "sh515220": {"qfqday": [["2026-08-%02d" % (d + 1), "1.2", str(round(1.2 + d * 0.005, 3)), "0", "0", "0"] for d in range(24)]},
        "sz000998": {"day": [["2026-08-%02d" % (d + 1), "9.0", str(9.0 + d * 0.1), "0", "0", "0"] for d in range(24)]},
    }
}


def _fake_fetch(url, params=None, timeout=6):
    if "hq.sinajs.cn" in url:
        wanted = set(url.split("list=")[1].split(","))
        text = "\n".join(l for l in SINA_TEXT.strip().split("\n")
                          if l.replace('var hq_str_', '').split('=')[0] in wanted)
        return SimpleNamespace(text=text, json=lambda: {}, encoding="utf-8")
    sym = params["param"].split(",")[0]
    data = {sym: KLINE_JSON_TEMPLATE["data"][sym]}
    return SimpleNamespace(text="", json=lambda: {"code": 0, "data": data}, encoding="utf-8")


def test_fetch_realtime_parse():
    rt = _fetch_realtime(_fake_fetch, ["sh000001", "sh515220", "szbad"])
    assert set(rt.keys()) == {"sh000001", "sh515220"}, rt
    name, cur, pct = rt["sh515220"]
    assert name == "煤炭ETF国泰" and cur == 1.298
    assert pct == round((1.298 / 1.291 - 1) * 100, 2)
    print("PASS: 新浪实时解析（含无效标的跳过）")


def test_build_snapshot_with_fake_fetch():
    text = build_market_snapshot(now_str="2026-09-05 18:30", fetch=_fake_fetch, sleep=lambda s: None)
    assert "上证指数" in text and "煤炭ETF" in text and "隆平高科" in text, text
    assert "2026-09-05 18:30" in text
    assert text.count("大盘参照") == 1
    idx_line = [l for l in text.splitlines() if "上证指数" in l][0]
    assert "5日" in idx_line and "30日" in idx_line
    # 隆平高科走 day（非 qfqday）字段也能解析出收盘
    lp = [l for l in text.splitlines() if "隆平高科" in l][0]
    assert "30日区间" in lp, lp
    print("PASS: 快照构建（格式与 qfqday/day 兼容）")


def test_build_snapshot_total_failure():
    def dead_fetch(url, params=None, timeout=6):
        raise OSError("network down")
    text = build_market_snapshot(now_str="x", fetch=dead_fetch, sleep=lambda s: None)
    assert text == "", f"全失败应返回空串，实际: {text!r}"
    print("PASS: 全失败返回空串")


def test_kline_failure_degrades_to_realtime():
    """K线挂但实时在 → 仍输出带『无K线历史』的行"""
    def mixed_fetch(url, params=None, timeout=6):
        if "hq.sinajs.cn" in url:
            return SimpleNamespace(text=SINA_TEXT, json=lambda: {}, encoding="utf-8")
        raise OSError("kline down")
    text = build_market_snapshot(now_str="t", fetch=mixed_fetch, sleep=lambda s: None)
    assert "煤炭ETF" in text and "无K线历史" in text, text
    print("PASS: K线失败降级为纯实时行")


if __name__ == "__main__":
    test_compute_stats_basic()
    test_compute_stats_short_series()
    test_fetch_realtime_parse()
    test_build_snapshot_with_fake_fetch()
    test_build_snapshot_total_failure()
    test_kline_failure_degrades_to_realtime()
    print("all checks passed")
