from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vp_agent import chat_api
from vp_agent.chat_api import app


E06_SENTENCE = (
    "Find customers whose total out-of-bundle data usage in the last 30 days "
    "is above a given threshold."
)
E06_QUESTION = (
    'When you say "out-of-bundle data usage," do you mean the customer\'s '
    "out-of-bundle data spend/charges over the last 30 days, or their total data "
    "volume consumed in the last 30 days regardless of whether it was in-bundle "
    "or out-of-bundle?"
)
E06_REPHRASED = (
    "Find customers whose total out-of-bundle data volume in the last 30 days "
    "is above a given threshold."
)
E06_CONDITION = (
    "INSTFCTDATE >= CurrentTime-30DAYS AND INSTFCTDATE < CurrentTime "
    "AND SUM(Data_Outbundle_Usage) ${operator} ${value}"
)


def _clarification_response(request_id: str) -> dict:
    return {
        "ok": False,
        "request_id": request_id,
        "parent_condition": None,
        "needs_clarification": True,
        "clarification_question": E06_QUESTION,
        "failure_reason": "Clarification needed before rendering.",
        "warnings": [],
    }


def _success_response(request_id: str) -> dict:
    return {
        "ok": True,
        "request_id": request_id,
        "parent_condition": E06_CONDITION,
        "selected_columns": ["Data_Outbundle_Usage"],
        "selected_vps": [],
        "seed": "seed-123",
        "needs_clarification": False,
        "clarification_question": None,
        "warnings": [],
    }


@pytest.fixture(autouse=True)
def isolate_session_log(monkeypatch, tmp_path):
    """Keep tests out of the real outputs/chat_sessions.jsonl."""
    monkeypatch.setattr(chat_api, "SESSION_LOG", tmp_path / "chat_sessions.jsonl")


@pytest.fixture
def stub_backend(monkeypatch):
    """Replace the VP agent with a script, and record every sentence it sees."""
    sent: list[str] = []

    async def fake_call(client, sentence, session_id, request_id):
        sent.append(sentence)
        if len(sent) == 1:
            return _clarification_response(request_id)
        return _success_response(request_id)

    async def fake_rephrase(prompt):
        return E06_REPHRASED

    monkeypatch.setattr(chat_api, "_call_vp_build", fake_call)
    monkeypatch.setattr(chat_api, "_query_rephraser", fake_rephrase)
    monkeypatch.setattr(chat_api, "SESSIONS", {})
    return sent


def test_clarification_loop_sends_only_a_clean_rephrased_sentence(stub_backend):
    client = TestClient(app)

    session_id = client.post("/api/session", json={"client": "omantel"}).json()["session_id"]

    first = client.post(f"/api/session/{session_id}/ask", json={"sentence": E06_SENTENCE}).json()
    assert first["branch"] == "clarification"
    assert first["response"]["clarification_question"] == E06_QUESTION
    assert first["clarification_round"] == 1

    rephrased = client.post(
        f"/api/session/{session_id}/rephrase",
        json={"answer": "data volume consumed, not charges"},
    ).json()
    assert rephrased["sentence"] == E06_REPHRASED
    assert rephrased["warnings"] == []

    second = client.post(
        f"/api/session/{session_id}/ask", json={"sentence": rephrased["sentence"]}
    ).json()
    assert second["branch"] == "resolved"
    assert second["response"]["parent_condition"] == E06_CONDITION

    # The contract: the VP agent sees two plain requests, never the Q&A.
    assert stub_backend == [E06_SENTENCE, E06_REPHRASED]
    for sentence in stub_backend:
        assert "Clarification" not in sentence
        assert "Answer" not in sentence
        assert E06_QUESTION not in sentence


def test_rephrase_is_not_sent_until_the_user_asks(stub_backend):
    client = TestClient(app)
    session_id = client.post("/api/session", json={"client": "omantel"}).json()["session_id"]
    client.post(f"/api/session/{session_id}/ask", json={"sentence": E06_SENTENCE})

    client.post(f"/api/session/{session_id}/rephrase", json={"answer": "volume"})

    # One backend call so far: /rephrase must not reach the VP agent on its own.
    assert len(stub_backend) == 1


def test_answering_the_same_round_twice_replaces_the_answer(stub_backend):
    client = TestClient(app)
    session_id = client.post("/api/session", json={"client": "omantel"}).json()["session_id"]
    client.post(f"/api/session/{session_id}/ask", json={"sentence": E06_SENTENCE})

    client.post(f"/api/session/{session_id}/rephrase", json={"answer": "charges"})
    client.post(f"/api/session/{session_id}/rephrase", json={"answer": "volume"})

    session = client.get(f"/api/session/{session_id}").json()
    assert session["answers"] == ["volume"]


def test_rephrase_always_rebuilds_from_the_original_sentence(monkeypatch):
    """Round N must rewrite the original, never a previous rewrite."""
    prompts: list[str] = []

    async def capture(prompt):
        prompts.append(prompt)
        return "Rewritten sentence number " + str(len(prompts))

    calls = {"n": 0}

    async def fake_call(client, sentence, session_id, request_id):
        calls["n"] += 1
        return _clarification_response(request_id)

    monkeypatch.setattr(chat_api, "_query_rephraser", capture)
    monkeypatch.setattr(chat_api, "_call_vp_build", fake_call)
    monkeypatch.setattr(chat_api, "SESSIONS", {})

    client = TestClient(app)
    session_id = client.post("/api/session", json={"client": "omantel"}).json()["session_id"]
    client.post(f"/api/session/{session_id}/ask", json={"sentence": E06_SENTENCE})
    client.post(f"/api/session/{session_id}/rephrase", json={"answer": "volume"})
    first_rewrite = "Rewritten sentence number 1"
    client.post(f"/api/session/{session_id}/ask", json={"sentence": first_rewrite})
    client.post(f"/api/session/{session_id}/rephrase", json={"answer": "exclude roaming"})

    assert len(prompts) == 2
    # Both prompts are anchored on the original; neither feeds back the rewrite.
    for prompt in prompts:
        assert f"Original request: {E06_SENTENCE}" in prompt
    assert first_rewrite not in prompts[1]
    assert "Answer 1: volume" in prompts[1]
    assert "Answer 2: exclude roaming" in prompts[1]


def test_hard_failure_is_its_own_branch(monkeypatch):
    async def fake_call(client, sentence, session_id, request_id):
        return {
            "ok": False,
            "request_id": request_id,
            "parent_condition": None,
            "needs_clarification": False,
            "clarification_question": None,
            "failure_reason": "The agent did not call mcp__vp__render_condition.",
            "diagnostics": {"render_condition_called": False},
            "warnings": [],
        }

    monkeypatch.setattr(chat_api, "_call_vp_build", fake_call)
    monkeypatch.setattr(chat_api, "SESSIONS", {})

    client = TestClient(app)
    session_id = client.post("/api/session", json={"client": "omantel"}).json()["session_id"]
    result = client.post(f"/api/session/{session_id}/ask", json={"sentence": E06_SENTENCE}).json()

    assert result["branch"] == "failed"
    assert result["response"]["diagnostics"]["render_condition_called"] is False


def test_guard_flags_a_dropped_time_window():
    warnings = chat_api._guard_rephrase(
        E06_SENTENCE,
        "Find customers whose total out-of-bundle data volume is above a given threshold.",
        ["volume"],
    )
    assert any("time window" in w for w in warnings)


def test_guard_flags_a_dropped_threshold():
    warnings = chat_api._guard_rephrase(
        E06_SENTENCE,
        "Find customers with out-of-bundle data volume in the last 30 days.",
        ["volume"],
    )
    assert any("threshold" in w for w in warnings)


def test_guard_flags_invented_numbers():
    warnings = chat_api._guard_rephrase(
        E06_SENTENCE,
        "Find customers whose out-of-bundle data volume in the last 30 days is above 500.",
        ["volume"],
    )
    assert any("introduced numbers" in w for w in warnings)


def test_guard_accepts_numbers_that_came_from_the_answer():
    warnings = chat_api._guard_rephrase(
        E06_SENTENCE,
        "Find customers whose out-of-bundle data volume in the last 30 days is above 500.",
        ["use 500 as the threshold"],
    )
    assert not any("introduced numbers" in w for w in warnings)


def test_guard_passes_a_faithful_rewrite():
    assert chat_api._guard_rephrase(E06_SENTENCE, E06_REPHRASED, ["volume"]) == []


def test_rephrase_failure_falls_back_to_the_original_with_a_warning(monkeypatch):
    async def boom(prompt):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(chat_api, "_query_rephraser", boom)
    sentence, warnings = _run(chat_api._rephrase_sentence(E06_SENTENCE, [(E06_QUESTION, "volume")]))

    # Falling back to the Q&A template would violate the contract, so the
    # fallback is the untouched original plus an instruction to edit it.
    assert sentence == E06_SENTENCE
    assert warnings and "Rephrase failed" in warnings[0]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Find customers who spend a lot.  ", "Find customers who spend a lot."),
        ('"Find customers who spend a lot."', "Find customers who spend a lot."),
        ("Rewritten sentence: Find customers who spend a lot.", "Find customers who spend a lot."),
        ("`Find customers who spend a lot.`", "Find customers who spend a lot."),
        ("Find  customers\nwho spend a lot.", "Find customers who spend a lot."),
    ],
)
def test_clean_rephrase_strips_model_wrapping(raw, expected):
    assert chat_api._clean_rephrase(raw) == expected


def test_unknown_session_is_rejected():
    client = TestClient(app)
    assert client.post("/api/session/nope/ask", json={"sentence": "hi"}).status_code == 404


def _run(coro):
    import asyncio

    return asyncio.run(coro)
