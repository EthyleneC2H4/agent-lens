"""Phase A：真实通路加固 —— 重试退避 / token 记账 / 回落语义（全离线）。"""

import pytest

from lens.provider import (
    ChatResult,
    MockProvider,
    OpenAICompatibleProvider,
    ProviderUnavailable,
    _Retryable,
    select_provider,
)


def _provider(transport, sleeps, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("AGENTLENS_API_KEY", "test-key")
    return OpenAICompatibleProvider(
        base_url="https://fake.example/v1",
        model="fake",
        transport=transport,
        sleep=sleeps.append,
        max_retries=3,
        backoff_base_s=1.0,
    )


def test_provider_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(body):  # 429 两次后成功
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _Retryable("HTTP 429")
        return {
            "choices": [{"message": {"content": "pong"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2},
        }

    sleeps: list[float] = []
    res = _provider(flaky, sleeps, monkeypatch).chat([{"role": "user", "content": "hi"}])
    assert res.text == "pong" and calls["n"] == 3
    assert res.prompt_tokens == 7 and res.completion_tokens == 2
    assert sleeps == [1.0, 2.0]      # 指数退避：1s → 2s
    assert res.model == "fake" and res.latency_ms >= 0


def test_provider_honors_retry_after_header(monkeypatch):
    def rate_limited(body):
        raise _Retryable("HTTP 429", retry_after=7.5)

    sleeps: list[float] = []
    with pytest.raises(Exception, match="重试"):
        _provider(rate_limited, sleeps, monkeypatch).chat([{"role": "user", "content": "hi"}])
    assert sleeps == [7.5, 7.5, 7.5]  # Retry-After 优先于指数退避


def test_provider_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("AGENTLENS_API_KEY", raising=False)
    p = OpenAICompatibleProvider()
    assert not p.available
    with pytest.raises(ProviderUnavailable):
        p.chat([])


def test_select_provider_falls_back_to_mock(monkeypatch, capsys):
    monkeypatch.delenv("AGENTLENS_API_KEY", raising=False)
    prov = select_provider("real")
    assert type(prov).__name__ == "MockProvider"
    assert "回落 MockProvider" in capsys.readouterr().out
    assert type(select_provider("mock")).__name__ == "MockProvider"


def test_chatresult_total_and_mock_accounting():
    r = ChatResult(text="x", prompt_tokens=3, completion_tokens=4)
    assert r.total_tokens == 7
    m = MockProvider().chat([{"role": "user", "content": "hello world foo"}])
    assert m.prompt_tokens > 0 and m.completion_tokens > 0   # mock 也走记账演练


# ---------- P5 技术债 #7：MockProvider pairwise 分支（离线确定性） ----------


def test_mock_provider_pairwise_numeric_branch():
    """pairwise 形态 prompt：谁更接近参考答案选谁；格式差异宽容；等距 tie。"""
    m = MockProvider()

    def ask(gold, a, b):
        prompt = f"任务: q\n参考答案: {gold}\n候选 A: {a}\n候选 B: {b}\n只回答 A/B/tie:"
        return m.chat([{"role": "user", "content": prompt}]).text

    assert ask("384", "384", "385") == "A"          # A 更接近
    assert ask("384", "385", "384") == "B"          # 交换位置后仍按质量选
    assert ask("384", "384", "384") == "tie"        # 同对同错
    assert ask("37.5%", "37.5 %", "37.6%") == "A"   # % 与空格宽容


# ---------- P6：http_transport 必须携带显式 User-Agent ----------


def test_http_transport_sends_explicit_user_agent(monkeypatch):
    """带 bot 防护的 OpenAI-compatible 网关会拦 Python-urllib 默认 UA
    （openzen 实测：无 UA 一律 403，curl/自定义 UA 均 200）——默认 transport
    必须自带显式 UA，否则真实通路在部分端点上不可用。"""
    import urllib.request

    from lens.provider import http_transport

    captured: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"choices": [{"message": {"content": "ok"}}], "usage": {}}'

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    send = http_transport("https://fake.example/v1", "test-key", 5)
    out = send({"model": "m", "messages": []})
    assert out["choices"][0]["message"]["content"] == "ok"
    ua = next(
        (v for k, v in captured["req"].headers.items() if k.lower() == "user-agent"), ""
    )
    assert ua and "python-urllib" not in ua.lower()


# ---------- P6：畸形响应归类为可重试网络错 ----------


def test_provider_retries_on_malformed_response(monkeypatch):
    """网关偶发返回缺 choices 的错误 JSON（free 池实测）：应走退避重试，
    而非让 KeyError 作为任务失败炸穿——重试耗尽后归一化为 NetworkError。"""
    calls = {"n": 0}

    def flaky(body):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"error": {"message": "upstream hiccup"}}   # 缺 choices
        return {
            "choices": [{"message": {"content": "A"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }

    sleeps: list[float] = []
    res = _provider(flaky, sleeps, monkeypatch).chat([{"role": "user", "content": "hi"}])
    assert res.text == "A" and calls["n"] == 2 and sleeps == [1.0]


def test_provider_malformed_exhausts_retries_as_network_error(monkeypatch):
    from lens.provider import NetworkError

    def always_bad(body):
        return {"object": "chat.completion"}   # 永远缺 choices

    sleeps: list[float] = []
    with pytest.raises(NetworkError, match="响应结构异常"):
        _provider(always_bad, sleeps, monkeypatch).chat([{"role": "user", "content": "hi"}])
