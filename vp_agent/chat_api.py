"""Session layer between the chat UI and the stateless /vp/build endpoint.

`vp_agent.api` starts a fresh agent for every call: there is no conversation
resume, and `session_id` only groups traces. So when the agent asks for a
clarification, the only channel for carrying the answer back is the `sentence`
string itself.

This layer owns that. It keeps the conversation, and when a clarification is
answered it rewrites the ORIGINAL request into one clean self-contained
sentence. The VP agent never sees the question, the answer, or any hint that a
previous attempt happened -- it receives a corrected first-time request.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from vp_agent.config import PROJECT_DIR, load_settings


VP_BUILD_URL = os.environ.get("VP_CHAT_BACKEND", "http://127.0.0.1:8000/vp/build")
VP_BUILD_TIMEOUT = 310.0  # just above the 300s cap the eval suite allows
MAX_CLARIFICATION_ROUNDS = 3
STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_LOG = PROJECT_DIR / "outputs" / "chat_sessions.jsonl"

REPHRASE_SYSTEM = """You rewrite telecom audience requests. You are given an
original request and the clarifications its reader asked for. Return ONE
self-contained sentence that folds the answers into the original request.

Hard rules:
- Preserve every time window exactly as written ("in the last 30 days").
- Preserve the threshold/comparison phrasing ("is above a given threshold").
- Never add a filter, product, channel, or condition the user did not state.
- Never mention that a question was asked or that this is a retry.
- Output the rewritten sentence only. No preamble, no quotes, no explanation."""


# --- session state -----------------------------------------------------------


@dataclass
class Turn:
    index: int
    sentence_sent: str
    request_id: str
    elapsed_sec: float
    branch: str
    response: dict[str, Any]


@dataclass
class Session:
    id: str
    client: str
    created_at: str
    status: str = "new"
    original_sentence: str | None = None
    questions: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SESSIONS: dict[str, Session] = {}


# --- request/response models -------------------------------------------------


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client: Literal["omantel", "airtel"]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentence: str = Field(min_length=1)


class RephraseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1)


app = FastAPI(
    title="VP Chat UI",
    description="Conversational front end for the VP parent-condition builder.",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "backend": VP_BUILD_URL, "sessions": len(SESSIONS)}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/session")
async def create_session(request: CreateSessionRequest) -> dict[str, Any]:
    session = Session(
        id=f"chat-{uuid.uuid4().hex[:8]}",
        client=request.client,
        created_at=_now(),
    )
    SESSIONS[session.id] = session
    return {"session_id": session.id, "client": session.client}


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    return _session(session_id).to_dict()


@app.post("/api/session/{session_id}/ask")
async def ask(session_id: str, request: AskRequest) -> dict[str, Any]:
    """Send one sentence to the VP agent and block until it answers.

    Whatever arrives here is forwarded verbatim. Turn 1 carries what the user
    typed; later turns carry the rephrase the user approved. Same code path
    either way, so what the UI displayed is provably what was sent.
    """
    session = _session(session_id)
    sentence = request.sentence.strip()
    if session.original_sentence is None:
        session.original_sentence = sentence

    turn_index = len(session.turns) + 1
    request_id = f"{session.id}-t{turn_index}"
    started = time.monotonic()
    response = await _call_vp_build(session.client, sentence, session.id, request_id)
    elapsed = round(time.monotonic() - started, 1)

    branch = _branch(response)
    session.turns.append(
        Turn(
            index=turn_index,
            sentence_sent=sentence,
            request_id=request_id,
            elapsed_sec=elapsed,
            branch=branch,
            response=response,
        )
    )
    session.status = branch

    if branch == "clarification":
        question = response.get("clarification_question") or ""
        session.questions.append(question)
    elif branch == "resolved":
        _log_resolved(session, sentence, response)

    return {
        "session_id": session.id,
        "turn": turn_index,
        "branch": branch,
        "elapsed_sec": elapsed,
        "sentence_sent": sentence,
        "clarification_round": len(session.questions),
        "max_rounds": MAX_CLARIFICATION_ROUNDS,
        "rounds_exhausted": len(session.questions) > MAX_CLARIFICATION_ROUNDS,
        "response": response,
    }


@app.post("/api/session/{session_id}/rephrase")
async def rephrase(session_id: str, request: RephraseRequest) -> dict[str, Any]:
    """Fold a clarification answer into the original request.

    Returns the candidate sentence and stops. Nothing reaches the VP agent
    until the user approves it via /ask.
    """
    session = _session(session_id)
    if not session.original_sentence:
        raise HTTPException(status_code=409, detail="Session has no original sentence yet.")
    if not session.questions:
        raise HTTPException(status_code=409, detail="No clarification question is pending.")

    # Replace rather than append, so answering the same round twice (the user
    # changed their mind) does not stack duplicate answers.
    round_index = len(session.questions) - 1
    session.answers = session.answers[:round_index] + [request.answer.strip()]

    pairs = list(zip(session.questions, session.answers))
    sentence, warnings = await _rephrase_sentence(session.original_sentence, pairs)
    return {
        "sentence": sentence,
        "warnings": warnings,
        "round": len(session.questions),
        "max_rounds": MAX_CLARIFICATION_ROUNDS,
        "original_sentence": session.original_sentence,
    }


# --- VP backend --------------------------------------------------------------


async def _call_vp_build(client: str, sentence: str, session_id: str, request_id: str) -> dict[str, Any]:
    payload = {
        "client": client,
        "sentence": sentence,
        "session_id": session_id,
        "request_id": request_id,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(VP_BUILD_TIMEOUT)) as http:
            result = await http.post(VP_BUILD_URL, json=payload)
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach the VP agent at {VP_BUILD_URL}. Is ./scripts/run_api.sh running?",
        ) from exc
    except httpx.ReadTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail=f"The VP agent did not answer within {int(VP_BUILD_TIMEOUT)}s.",
        ) from exc
    if result.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"VP agent returned {result.status_code}: {result.text[:500]}")
    return result.json()


def _branch(response: dict[str, Any]) -> str:
    if response.get("parent_condition"):
        return "resolved"
    if response.get("needs_clarification") and response.get("clarification_question"):
        return "clarification"
    return "failed"


# --- rephraser ---------------------------------------------------------------


async def _rephrase_sentence(original: str, pairs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """Rebuild the request from the ORIGINAL plus every answer so far.

    Never rewrites a previous rewrite -- one rewrite from source each round, so
    drift cannot compound as rounds stack.
    """
    lines = [f"Original request: {original}", ""]
    for index, (question, answer) in enumerate(pairs, start=1):
        lines.append(f"Question {index}: {question}")
        lines.append(f"Answer {index}: {answer}")
    lines.append("")
    lines.append("Rewritten sentence:")
    prompt = "\n".join(lines)

    try:
        rewritten = await _query_rephraser(prompt)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, never silent
        return original, [
            f"Rephrase failed ({type(exc).__name__}). Showing your original sentence unchanged - "
            "edit it by hand to fold in your answer before sending."
        ]

    rewritten = _clean_rephrase(rewritten)
    if not rewritten:
        return original, [
            "The rephraser returned nothing usable. Showing your original sentence unchanged - "
            "edit it by hand before sending."
        ]
    return rewritten, _guard_rephrase(original, rewritten, [answer for _, answer in pairs])


async def _query_rephraser(prompt: str) -> str:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    options = ClaudeAgentOptions(
        model=load_settings().subagent_model,
        system_prompt=REPHRASE_SYSTEM,
        # No project settings, no skills, no MCP, no tools. This is a single
        # rewrite, not an agent; loading the VP toolchain here would be both
        # slow and a way for rule syntax to leak into a plain-English sentence.
        setting_sources=[],
        tools=[],
        allowed_tools=[],
        max_turns=1,
    )
    chunks: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    chunks.append(block.text.strip())
    return "\n".join(chunks).strip()


def _clean_rephrase(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    # The model occasionally wraps the sentence or prefixes a label despite the
    # system prompt; strip those rather than sending them to the VP agent.
    text = re.sub(r"^(?:rewritten sentence|sentence|output)\s*:\s*", "", text, flags=re.I).strip()
    text = text.strip("`").strip()
    if len(text) > 1 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()
    return " ".join(text.split())


TIME_WINDOW_RE = re.compile(
    r"\b(?:last|past|previous)\s+\d+\s*(?:day|days|week|weeks|month|months|year|years)\b"
    r"|\b\d+\s*(?:day|days|week|weeks|month|months|year|years)\b"
    r"|\b(?:last|past|this|current)\s+(?:day|week|month|quarter|year)\b",
    re.I,
)
THRESHOLD_RE = re.compile(
    r"\b(?:above|below|over|under|greater|less|fewer|more than|at least|at most|"
    r"exceed\w*|threshold|between|equal to|higher|lower)\b",
    re.I,
)
DIGIT_RE = re.compile(r"\d+")


def _guard_rephrase(original: str, rewritten: str, answers: list[str]) -> list[str]:
    """Deterministic old-vs-new checks.

    These never block the send. They flag the rephrases worth actually reading,
    so a lost time window shows up as a banner rather than as a rule with no
    INSTFCTDATE bounds.
    """
    warnings: list[str] = []

    if TIME_WINDOW_RE.search(original) and not TIME_WINDOW_RE.search(rewritten):
        warnings.append("The time window from your original request is missing in the rewrite.")

    if THRESHOLD_RE.search(original) and not THRESHOLD_RE.search(rewritten):
        warnings.append("The threshold/comparison wording from your original request is missing in the rewrite.")

    known = set(DIGIT_RE.findall(original))
    for answer in answers:
        known.update(DIGIT_RE.findall(answer))
    invented = [number for number in DIGIT_RE.findall(rewritten) if number not in known]
    if invented:
        warnings.append(f"The rewrite introduced numbers you never mentioned: {', '.join(sorted(set(invented)))}.")

    if len(rewritten) < 0.6 * len(original):
        warnings.append("The rewrite is much shorter than your original request; check nothing was dropped.")

    return warnings


# --- misc --------------------------------------------------------------------


def _session(session_id: str) -> Session:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session. Reload the page to start a new one.")
    return session


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_resolved(session: Session, sentence: str, response: dict[str, Any]) -> None:
    """Append the resolved pair to JSONL.

    Sessions live in memory and die with the process, but a clarified sentence
    paired with a rendered condition is exactly the shape golden_case.csv wants,
    so that part is worth keeping.
    """
    record = {
        "session_id": session.id,
        "client": session.client,
        "resolved_at": _now(),
        "original_sentence": session.original_sentence,
        "final_sentence": sentence,
        "clarification_rounds": len(session.questions),
        "questions": session.questions,
        "answers": session.answers,
        "parent_condition": response.get("parent_condition"),
        "selected_columns": response.get("selected_columns"),
        "seed": response.get("seed"),
        "request_id": response.get("request_id"),
    }
    try:
        SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SESSION_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # logging must never break the request
