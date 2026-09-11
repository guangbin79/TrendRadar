# coding=utf-8
"""RSS fetcher 代理↔直连交替重试的自检脚本

运行: uv run python tests/test_rss_fetcher_fallback.py
场景: 机场节点对境外源间歇性 SSL 重置（同一域名随时间成败波动），
      代理失败后应有直连兜底（未被墙的站直连可通），以及再次代理重试。
"""

import requests

from trendradar.crawler.rss.fetcher import RSSFetcher, RSSFeedConfig

RSS_XML = (
    '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
    '<item><title>hello world</title><link>https://example.com/a</link></item>'
    '</channel></rss>'
)

FEED = RSSFeedConfig(id="demo", name="Demo", url="https://example.com/feed")


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class OkSession:
    def get(self, url, timeout=0):
        return FakeResponse(RSS_XML)


class DeadSession:
    def __init__(self, exc=None):
        self.exc = exc or requests.exceptions.SSLError("EOF in violation of protocol")

    def get(self, url, timeout=0):
        raise self.exc


def make_fetcher():
    return RSSFetcher([FEED], use_proxy=True, proxy_url="http://172.18.0.1:7897")


def test_proxy_fail_falls_back_to_direct():
    """代理会话 SSL 失败 → 直连会话成功 → 返回条目而非错误"""
    import trendradar.crawler.rss.fetcher as mod
    orig_sleep = mod.time.sleep
    mod.time.sleep = lambda s: None
    try:
        f = make_fetcher()
        f.session = DeadSession()          # 代理路径失败
        f.direct_session = OkSession()     # 直连路径成功
        items, err = f.fetch_feed(FEED)
        assert err is None, f"应无错误，实际: {err}"
        assert items and items[0].title == "hello world", f"应解析出条目，实际: {items}"
    finally:
        mod.time.sleep = orig_sleep
    print("PASS: 代理失败回退直连")


def test_retry_reuses_proxy_after_direct():
    """直连也失败（被墙站）→ 第二次代理尝试成功 → 仍能拿到条目"""
    import trendradar.crawler.rss.fetcher as mod
    orig_sleep = mod.time.sleep
    mod.time.sleep = lambda s: None
    try:
        f = make_fetcher()
        # 代理第 1 次失败、直连失败、代理第 2 次成功
        # 代理第 1 次失败、直连失败、代理第 2 次成功
        class FlakyProxy:
            calls = [DeadSession(), OkSession()]

            def get(self, url, timeout=0):
                return self.calls.pop(0).get(url, timeout)

        f.session = FlakyProxy()
        f.direct_session = DeadSession(requests.exceptions.ConnectionError("blocked"))
        items, err = f.fetch_feed(FEED)
        assert err is None, f"应无错误，实际: {err}"
        assert items, "第二次代理尝试应成功"
    finally:
        mod.time.sleep = orig_sleep
    print("PASS: 直连失败后再次代理重试")


def test_all_paths_fail_returns_error():
    """所有尝试都失败 → 返回错误信息"""
    import trendradar.crawler.rss.fetcher as mod
    orig_sleep = mod.time.sleep
    mod.time.sleep = lambda s: None
    try:
        f = make_fetcher()
        f.session = DeadSession()
        f.direct_session = DeadSession(requests.exceptions.ConnectionError("blocked"))
        items, err = f.fetch_feed(FEED)
        assert items == [] and err, "应返回空条目和错误信息"
    finally:
        mod.time.sleep = orig_sleep
    print("PASS: 全部失败时返回错误")


if __name__ == "__main__":
    test_proxy_fail_falls_back_to_direct()
    test_retry_reuses_proxy_after_direct()
    test_all_paths_fail_returns_error()
    print("all checks passed")
