"""The sample contract is read when the server starts, so the first visitor's click
is instant (10 Nov 2026: the first click after a restart waited 12 s)."""
from fastapi.testclient import TestClient

from app import server


def _fresh(monkeypatch, reader):
    monkeypatch.setattr(server, "_sample_sched", None)
    monkeypatch.setattr(server, "ai_configured", lambda: True)
    monkeypatch.setattr(server, "read_contract", reader)
    server._hits.clear()
    server._daily.clear()


def test_the_sample_is_read_once_at_start_and_the_first_click_makes_no_model_call(monkeypatch, schedule):
    calls = []

    async def reader(data: bytes):
        calls.append(len(data))
        return schedule

    _fresh(monkeypatch, reader)
    with TestClient(server.app) as client:
        assert len(calls) == 1
        first = client.post("/api/contracts/sample")
        second = client.post("/api/contracts/sample")
    assert first.status_code == 200 and second.status_code == 200
    assert len(calls) == 1  # neither click read the contract again
    assert first.json()["id"] != second.json()["id"]  # each visitor gets their own workspace
    assert len(first.json()["schedule"]["milestones"]) == len(schedule.milestones)


def test_a_failing_read_at_start_does_not_stop_the_server_and_the_first_click_reads_it(monkeypatch, schedule):
    state = {"fail": True, "calls": 0}

    async def reader(data: bytes):
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("model service down")
        return schedule

    _fresh(monkeypatch, reader)
    with TestClient(server.app) as client:
        assert client.get("/").status_code == 200
        state["fail"] = False
        assert client.post("/api/contracts/sample").status_code == 200
    assert state["calls"] == 2


def test_no_read_at_start_without_a_model_key_or_when_switched_off(monkeypatch, schedule):
    calls = []

    async def reader(data: bytes):
        calls.append(1)
        return schedule

    _fresh(monkeypatch, reader)
    monkeypatch.setattr(server, "ai_configured", lambda: False)
    with TestClient(server.app):
        pass
    monkeypatch.setattr(server, "ai_configured", lambda: True)
    monkeypatch.setattr(server, "SAMPLE_AT_START", False)
    with TestClient(server.app):
        pass
    assert calls == []
