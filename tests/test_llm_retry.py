"""A model call that stalls gets one fresh try inside the check's time limit (5 Nov 2026:
a delivery check that answers in 7 s once waited out the whole 90 s limit and failed)."""
import asyncio

import pytest
from pydantic import BaseModel

from app import agent, evidence, llm


class Out(BaseModel):
    answer: str


class _Resp:
    def __init__(self, value):
        self.parsed = value
        self.text = value.model_dump_json()


class _Models:
    def __init__(self, script):
        self.script, self.calls = list(script), 0

    async def generate_content(self, **kw):
        self.calls += 1
        step = self.script.pop(0)
        if step == "stall":
            await asyncio.sleep(5)
        if isinstance(step, Exception):
            raise step
        return _Resp(Out(answer="ok"))


class _Client:
    def __init__(self, script):
        self.aio = type("Aio", (), {})()
        self.aio.models = _Models(script)


class _ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"error {code}")
        self.code = code


def _with(monkeypatch, script):
    client = _Client(script)
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    return client.aio.models


def test_a_stalled_call_gets_one_fresh_try_that_answers(monkeypatch):
    models = _with(monkeypatch, ["stall", "ok"])
    out = asyncio.run(llm.generate_json("p", Out, attempt_seconds=0.05, attempts=2))
    assert out.answer == "ok" and models.calls == 2


def test_two_stalled_calls_give_up_with_a_timeout(monkeypatch):
    models = _with(monkeypatch, ["stall", "stall"])
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(llm.generate_json("p", Out, attempt_seconds=0.05, attempts=2))
    assert models.calls == 2


def test_a_rate_limit_gets_the_fresh_try(monkeypatch):
    models = _with(monkeypatch, [_ApiError(429), "ok"])
    assert asyncio.run(llm.generate_json("p", Out, attempt_seconds=1, attempts=2)).answer == "ok"
    assert models.calls == 2


def test_a_request_error_is_not_tried_again(monkeypatch):
    models = _with(monkeypatch, [_ApiError(400), "ok"])
    with pytest.raises(_ApiError):
        asyncio.run(llm.generate_json("p", Out, attempt_seconds=1, attempts=2))
    assert models.calls == 1


def test_without_attempt_settings_the_call_runs_once_unbounded(monkeypatch):
    models = _with(monkeypatch, [_ApiError(503), "ok"])
    with pytest.raises(_ApiError):
        asyncio.run(llm.generate_json("p", Out))
    assert models.calls == 1


def test_two_tries_fit_inside_the_check_time_limit():
    from app import server
    assert llm.CHECK_ATTEMPTS * llm.CHECK_ATTEMPT_SECONDS < server.CHECK_TIMEOUT


@pytest.mark.parametrize("module", [agent, evidence])
def test_both_checks_ask_for_the_retry(module):
    import inspect
    src = inspect.getsource(module.check_delivery if module is agent else module.check_evidence)
    assert "attempt_seconds=CHECK_ATTEMPT_SECONDS" in src and "attempts=CHECK_ATTEMPTS" in src
