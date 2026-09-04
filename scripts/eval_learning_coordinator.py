#!/usr/bin/env python3
"""Paired teaching-quality evaluation for the Learning Coordinator."""

from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

REQUIRED_DOMAINS = frozenset(
    {"mathematics", "physical_science", "programming", "humanities", "open_analysis"}
)
REQUIRED_SCOPES = frozenset({"answer", "lesson", "path"})
FORBIDDEN_ANSWER_KEYS = frozenset({"preferred_answer", "reference_answer", "expected_answer"})
SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "access_token",
        "refresh_token",
        "auth_token",
        "id_token",
        "token",
        "secret",
        "password",
        "authorization",
        "credential",
        "credentials",
    }
)
SENSITIVE_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "auth_token",
        "id_token",
        "token",
        "key",
        "secret",
        "password",
        "credential",
        "credentials",
    }
)
DEFAULT_PROVIDER_SEED = 317
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CASES_PATH = ROOT / "evals" / "learning_coordinator" / "cases.jsonl"
DEFAULT_RUBRIC_PATH = ROOT / "evals" / "learning_coordinator" / "rubric.yaml"


class EvalCase:
    """One versioned request fixture plus deterministic expected contracts."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.id = str(payload["id"])
        self.version = int(payload["version"])
        self.domain = str(payload["domain"])
        self.scope = str(payload["scope"])
        self.prompt = str(payload["prompt"])
        self.expected = dict(payload["expected"])
        self.misconception = bool(payload.get("misconception", False))
        self.stuck_signal = bool(payload.get("stuck_signal", False))
        self.direct_answer = bool(payload.get("direct_answer", False))
        self.attached_only = bool(payload.get("attached_only", False))
        self.missing_tools = tuple(str(item) for item in payload.get("missing_tools", []))
        self.interrupted = bool(payload.get("interrupted", False))
        self.delayed_recall = bool(payload.get("delayed_recall", False))
        self.follow_up_prompt = str(payload.get("follow_up_prompt", ""))
        self.sources = tuple(dict(item) for item in payload.get("sources", []))
        self.stored_turn_state = dict(payload.get("stored_turn_state", {}))


class EvalResult:
    """Normalized observable result from one evaluated turn."""

    def __init__(
        self,
        *,
        status: str,
        scope: str = "",
        approval_requested: bool = False,
        path_created_before_approval: bool = False,
        final_help_level: int | None = None,
        direct_answer_honored: bool | None = None,
        source_ids: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
        route: str = "",
        route_available: bool = True,
        resumed_from_state: dict[str, Any] | None = None,
        assessment_valid: bool | None = None,
        mastery_before: dict[str, Any] | None = None,
        mastery_after: dict[str, Any] | None = None,
        decision_persisted: bool = False,
        thread_state: dict[str, Any] | None = None,
        persisted_evidence_count: int = 0,
        activity_consumed: bool = False,
        review_material: str = "",
        raw_output_ref: str = "",
        blocked_reason: str = "",
        latency_ms: float | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        self.status = status
        self.scope = scope
        self.approval_requested = approval_requested
        self.path_created_before_approval = path_created_before_approval
        self.final_help_level = final_help_level
        self.direct_answer_honored = direct_answer_honored
        self.source_ids = list(source_ids or [])
        self.evidence = dict(evidence) if evidence is not None else None
        self.route = route
        self.route_available = route_available
        self.resumed_from_state = dict(resumed_from_state or {})
        self.assessment_valid = assessment_valid
        self.mastery_before = dict(mastery_before or {})
        self.mastery_after = dict(mastery_after or {})
        self.decision_persisted = decision_persisted
        self.thread_state = dict(thread_state or {})
        self.persisted_evidence_count = int(persisted_evidence_count)
        self.activity_consumed = activity_consumed
        self.review_material = review_material
        self.raw_output_ref = raw_output_ref
        self.blocked_reason = blocked_reason
        self.latency_ms = latency_ms
        self.token_usage = dict(token_usage or {})


class ContractScore:
    """Deterministic pass/fail outcome for one result."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        self.passed = not failures


class UnavailableProviderRunner:
    """Produce explicit blocked records when no usable provider is configured."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def run(
        self,
        case: EvalCase,
        coordinator_mode: str,
        fixture: dict[str, Any],
        resume_state: dict[str, Any] | None,
    ) -> EvalResult:
        del coordinator_mode, fixture
        help_level = case.expected.get("help_level", case.expected.get("minimum_help_level", 0))
        return EvalResult(
            status="blocked",
            scope=case.scope,
            approval_requested=bool(case.expected["requires_approval"]),
            final_help_level=int(help_level),
            direct_answer_honored=None,
            source_ids=list(case.expected.get("allowed_source_ids", [])),
            route="unavailable" if not case.expected["route_available"] else "chat",
            route_available=bool(case.expected["route_available"]),
            resumed_from_state=dict(case.expected.get("resume_state", {})) if resume_state else {},
            blocked_reason=self._reason,
            latency_ms=0.0,
            token_usage={
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        )


def _mastery_snapshot(store: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for path_id in store.list_all():
        progress = store.load(path_id)
        snapshot[path_id] = progress.model_dump(mode="json") if progress is not None else None
    return snapshot


def _outgoing_attachments(case: EvalCase) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Build only client-authorized attachment fields plus a logical-name map."""

    attachments: list[dict[str, str]] = []
    source_by_filename: dict[str, str] = {}
    for source in case.sources:
        source_id = str(source.get("id") or "").strip()
        text = str(source.get("text") or "")
        filename = str(source.get("filename") or source_id)
        if not source_id:
            continue
        attachments.append(
            {
                "type": "file",
                "filename": filename,
                "mime_type": "text/plain",
                "base64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            }
        )
        source_by_filename[filename] = source_id
    return attachments, source_by_filename


def _citation_ids(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, dict):
        identifiers = {
            str(value.get(field) or "").strip()
            for field in ("id", "citation_id", "source_id", "reference_id")
        }
        identifiers.discard("")
        if identifiers:
            return identifiers
        return {item for nested in value.values() for item in _citation_ids(nested)}
    if isinstance(value, list):
        return {item for nested in value for item in _citation_ids(nested)}
    return set()


def _bare_result_text(response: str) -> str:
    """Remove only presentation wrappers around a possible bare result."""

    candidate = response.strip()
    candidate = re.sub(r"^(?:>\s*)+", "", candidate)
    candidate = re.sub(r"^#{1,6}\s+", "", candidate)
    wrappers = (
        ("$$", "$$"),
        ("$", "$"),
        (r"\[", r"\]"),
        (r"\(", r"\)"),
        ("**", "**"),
        ("__", "__"),
        ("`", "`"),
        ("(", ")"),
        ("[", "]"),
        ("{", "}"),
        ('"', '"'),
        ("'", "'"),
        ("“", "”"),
        ("‘", "’"),
    )
    surrounding_punctuation = " \t\r\n.,;:!。；：！，"
    while candidate:
        previous = candidate
        candidate = candidate.strip(surrounding_punctuation)
        for opening, closing in wrappers:
            if (
                candidate.startswith(opening)
                and candidate.endswith(closing)
                and len(candidate) > len(opening) + len(closing)
            ):
                candidate = candidate[len(opening) : -len(closing)].strip()
                break
        if candidate == previous:
            break
    return candidate


def _response_gives_direct_answer(response: str) -> bool:
    """Certify only a question-free, unhedged labelled or bare result."""

    normalized = re.sub(r"[`*_>#]", " ", response).strip()
    if not normalized or "?" in normalized or "？" in normalized:
        return False
    hedged = re.compile(
        r"(?i)(?:\b(?:maybe|perhaps|possibly|probably|likely|apparently|might|may|"
        r"could|would|try|guess|suppose|seems?)\b|"
        r"也许|可能|或许|大概|恐怕|似乎|看起来|试着|试试|猜(?:测)?|不妨)"
    )
    if hedged.search(normalized):
        return False
    explicit_answer = re.compile(
        r"(?i)(?:"
        r"\b(?:the\s+)?(?:direct\s+)?(?:answer|result|solution|conclusion)\b\s*"
        r"(?:(?:is|are|equals)\s+|[:=]\s*)\S|"
        r"(?:答案|结果|结论|解)\s*(?:是|为|等于|[:：=])\s*\S)"
    )
    explicit_value = re.compile(
        r"(?i)(?:\b(?:value|amount|total|output)\b\s*"
        r"(?:(?:is|are|equals)\s+|[:=]\s*)\S|"
        r"(?:值|数值|总数|输出)\s*(?:是|为|等于|[:：=])\s*\S)"
    )
    number = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    atom = rf"(?:{number}|[A-Za-z_][A-Za-z0-9_]*|[π∞])"
    operator = r"(?:\*\*|[+*/^×÷·-])"
    expression = rf"[-+]?\s*{atom}(?:\s*{operator}\s*[-+]?\s*{atom})*"
    bare_assignment_or_equality = re.compile(
        rf"{expression}\s*=\s*{expression}",
        re.IGNORECASE,
    )
    bare_value = re.compile(
        rf"(?:[-+]?\s*{number}\s*%?|"
        rf"[-+]?\s*{atom}(?:\s*{operator}\s*[-+]?\s*{atom})+)",
        re.IGNORECASE,
    )
    bare_result = _bare_result_text(response)
    return bool(
        explicit_answer.search(normalized)
        or explicit_value.search(normalized)
        or bare_assignment_or_equality.fullmatch(bare_result)
        or bare_value.fullmatch(bare_result)
    )


@contextmanager
def _provider_seed(seed: int, *, supported: bool):
    if not supported:
        yield
        return
    from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline

    original = AgenticChatPipeline._completion_kwargs

    def seeded(self, max_tokens: int) -> dict[str, Any]:
        return {**original(self, max_tokens), "seed": seed}

    with patch.object(AgenticChatPipeline, "_completion_kwargs", seeded):
        yield


class LocalDeepTutorRunner:
    """Run isolated arms through production turn preparation and finalization."""

    def __init__(
        self,
        *,
        runtime_root: Path,
        raw_output_dir: Path,
        seed_supported: bool,
    ) -> None:
        self._runtime_root = runtime_root
        self._raw_output_dir = raw_output_dir
        self._seed_supported = seed_supported

    @contextmanager
    def _isolated_runtime(self, store: Any, attachment_store: Any, coordinator_mode: str):
        modules = (
            "deeptutor.learning.coordinator.service",
            "deeptutor.learning.service",
            "deeptutor.capabilities.learning_coordinator.tools",
            "deeptutor.capabilities.mastery.binding",
            "deeptutor.capabilities.mastery.tools",
            "deeptutor.capabilities.mastery.loop",
        )
        with ExitStack() as stack:
            for module_name in modules:
                try:
                    module = __import__(module_name, fromlist=["LearningStore"])
                except ImportError:
                    continue
                if hasattr(module, "LearningStore"):
                    stack.enter_context(
                        patch.object(module, "LearningStore", lambda *_args, **_kwargs: store)
                    )
            storage = __import__("deeptutor.learning.storage", fromlist=["LearningStore"])
            stack.enter_context(
                patch.object(storage, "LearningStore", lambda *_args, **_kwargs: store)
            )
            storage_service = __import__(
                "deeptutor.services.storage", fromlist=["get_attachment_store"]
            )
            stack.enter_context(
                patch.object(storage_service, "get_attachment_store", lambda: attachment_store)
            )
            runtime_settings = __import__(
                "deeptutor.services.config.runtime_settings",
                fromlist=["load_system_settings"],
            )
            original_load_settings = runtime_settings.load_system_settings

            def isolated_settings() -> dict[str, Any]:
                return {
                    **dict(original_load_settings()),
                    "learning_coordinator_mode": (
                        "active" if coordinator_mode == "active" else "off"
                    ),
                }

            stack.enter_context(
                patch.object(runtime_settings, "load_system_settings", isolated_settings)
            )
            yield

    async def run(
        self,
        case: EvalCase,
        coordinator_mode: str,
        fixture: dict[str, Any],
        resume_state: dict[str, Any] | None,
    ) -> EvalResult:
        from deeptutor.learning.coordinator.models import LearningDecision
        from deeptutor.learning.models import EvidenceOutcome
        from deeptutor.learning.storage import LearningStore
        from deeptutor.runtime.registry.capability_registry import get_capability_registry
        from deeptutor.runtime.registry.tool_registry import get_tool_registry
        from deeptutor.services.session import SQLiteSessionStore
        from deeptutor.services.session.turn_runtime import TurnRuntimeManager
        from deeptutor.services.storage import LocalDiskAttachmentStore

        started = time.perf_counter()
        arm_root = self._runtime_root / f"arm-{secrets.token_hex(16)}"
        store = LearningStore(root=arm_root / "learning")
        session_store = SQLiteSessionStore(db_path=arm_root / "sessions.db")
        attachment_store = LocalDiskAttachmentStore(root=arm_root / "attachments")
        attachments, source_by_filename = _outgoing_attachments(case)
        registry = get_capability_registry()
        tool_registry = get_tool_registry()
        session_id = "eval-" + hashlib.sha256(case.id.encode("utf-8")).hexdigest()[:24]
        loaded_resume = self._store_resume_fixture(
            store,
            case,
            session_id,
            resume_state,
        )
        current_prompt = case.follow_up_prompt if case.delayed_recall else case.prompt
        payload = {
            "content": current_prompt,
            "capability": "chat",
            "session_id": session_id,
            "language": "en",
            "tools": [],
            "attachments": attachments,
            "knowledge_bases": [],
            "learning_coordinator": coordinator_mode == "active",
        }
        if loaded_resume:
            payload["learning_thread_id"] = loaded_resume["thread_id"]
        before = _mastery_snapshot(store)
        events: list[dict[str, Any]] = []
        preparation_error: Exception | None = None
        runtime = TurnRuntimeManager(store=session_store)

        async def skip_title_generation(**_kwargs: Any) -> None:
            return None

        runtime._maybe_generate_session_title = skip_title_generation
        turn: dict[str, Any] = {}
        messages: list[dict[str, Any]] = []
        try:
            with (
                self._isolated_runtime(store, attachment_store, coordinator_mode),
                _provider_seed(int(fixture["provider_seed"]), supported=self._seed_supported),
            ):
                await session_store.create_session(session_id=session_id)
                await self._seed_session_history(
                    session_store,
                    session_id=session_id,
                    resume_state=resume_state,
                    goal=str((resume_state or {}).get("goal") or case.prompt),
                )
                _session, turn = await runtime.start_turn(payload)
                async for event in runtime.subscribe_turn(turn["id"]):
                    events.append(event)
                messages = await session_store.get_messages(session_id)
        except Exception as exc:
            preparation_error = exc
            metadata: dict[str, Any] = {}
            error_code = getattr(exc, "error_code", None)
            if isinstance(error_code, str) and error_code:
                metadata["error_code"] = error_code
            events.append({"type": "error", "content": str(exc), "metadata": metadata})
        finally:
            await runtime.close(drain_timeout_seconds=1.0)

        decision_raw = next(
            (
                dict((item.get("metadata") or {})["learning_decision"])
                for item in events
                if isinstance((item.get("metadata") or {}).get("learning_decision"), dict)
            ),
            None,
        )
        decision = None
        if decision_raw is not None:
            decision_raw.pop("requested_capability", None)
            decision_raw.pop("active_capability", None)
            decision = LearningDecision.model_validate(decision_raw)

        actual_attachment_ids: dict[str, str] = {}
        for message in messages:
            for attachment in message.get("attachments") or []:
                filename = str(attachment.get("filename") or "")
                attachment_id = str(attachment.get("id") or "")
                if filename in source_by_filename and attachment_id:
                    actual_attachment_ids[attachment_id] = source_by_filename[filename]

        evidence_records = store.list_evidence()
        turn_id = str(turn.get("id") or "")
        new_evidence = [record for record in evidence_records if record.turn_id == turn_id]
        evidence_record = new_evidence[-1] if new_evidence else None
        evidence = (
            self._evidence_summary(evidence_record, actual_attachment_ids)
            if evidence_record is not None
            else None
        )
        assessment_valid = (
            evidence_record.outcome is not EvidenceOutcome.UNASSESSED
            if evidence_record is not None
            else None
        )
        thread = (
            store.get_learning_thread(decision.thread_id)
            if decision is not None and decision.thread_id
            else store.get_learning_thread(str((loaded_resume or {}).get("thread_id") or ""))
            if loaded_resume
            else None
        )
        thread_state = thread.model_dump(mode="json") if thread is not None else {}
        after = _mastery_snapshot(store)
        raw_ref = self._write_raw_output(events)
        done = next((item for item in reversed(events) if item.get("type") == "done"), {})
        errors = [str(item.get("content") or "") for item in events if item.get("type") == "error"]
        terminal_status = str((done.get("metadata") or {}).get("status") or "failed")
        status = "completed" if terminal_status == "completed" and not errors else "failed"
        if status == "failed" and self._provider_unavailable(events, preparation_error):
            status = "blocked"
        result_metadata = next(
            (
                item.get("metadata") or {}
                for item in reversed(events)
                if item.get("type") == "result"
            ),
            {},
        )
        cost_summary = result_metadata.get("metadata", {}).get("cost_summary", {})
        citation_ids = set()
        for item in events:
            metadata = item.get("metadata") or {}
            citation_ids.update(_citation_ids(metadata.get("citations")))
            citation_ids.update(_citation_ids(metadata.get("citation_ids")))
        logical_citation_ids = {
            actual_attachment_ids.get(citation_id, citation_id) for citation_id in citation_ids
        }
        active_capability = str(turn.get("capability") or "chat")
        planned_route = decision.route if decision is not None else active_capability
        route_available = registry.get(planned_route) is not None and all(
            tool_registry.get(name) is not None for name in case.missing_tools
        )
        help_level = decision.activity.help_level if decision is not None else 0
        persisted_response = next(
            (
                str(message.get("content") or "")
                for message in reversed(messages)
                if message.get("role") == "assistant"
            ),
            "",
        )
        response = str(result_metadata.get("response") or persisted_response)
        return EvalResult(
            status=status,
            scope=decision.scope.value if decision is not None else case.scope,
            approval_requested=(decision.requires_approval if decision is not None else False),
            path_created_before_approval=(case.scope == "path" and before != after),
            final_help_level=help_level,
            direct_answer_honored=(
                _response_gives_direct_answer(response) if case.direct_answer else None
            ),
            source_ids=sorted(logical_citation_ids),
            evidence=evidence,
            route=active_capability,
            route_available=route_available,
            resumed_from_state=loaded_resume,
            assessment_valid=assessment_valid,
            mastery_before=before,
            mastery_after=after,
            decision_persisted=decision_raw is not None,
            thread_state=thread_state,
            persisted_evidence_count=len(evidence_records),
            activity_consumed=bool(new_evidence),
            review_material=response,
            raw_output_ref=raw_ref,
            blocked_reason="; ".join(errors) if status == "blocked" else "",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            token_usage={
                "prompt_tokens": int(cost_summary.get("prompt_tokens") or 0),
                "completion_tokens": int(cost_summary.get("completion_tokens") or 0),
                "total_tokens": int(cost_summary.get("total_tokens") or 0),
            },
        )

    @staticmethod
    async def _seed_session_history(
        session_store: Any,
        *,
        session_id: str,
        resume_state: dict[str, Any] | None,
        goal: str,
    ) -> None:
        if resume_state is None:
            return
        await session_store.ensure_session(session_id)
        prior_messages = resume_state.get("prior_messages")
        if not isinstance(prior_messages, list):
            prior_messages = [
                {"role": "user", "content": goal},
                {"role": "assistant", "content": "A follow-up learning activity was scheduled."},
            ]
        for message in prior_messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip()
            content = str(message.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            await session_store.add_message(
                session_id=session_id,
                role=role,
                content=content,
                capability="chat",
            )

    @staticmethod
    def _evidence_summary(record: Any, source_ids: dict[str, str]) -> dict[str, Any]:
        return {
            "objective_id": record.objective_id or record.thread_id,
            "activity_id": record.activity_kind,
            "learner_response_ref": record.response_ref or f"chat-turn:{record.turn_id}:user",
            "response": record.response,
            "rubric": list(record.rubric),
            "outcome": record.outcome.value,
            "help_level": record.help_level,
            "source_refs": [source_ids.get(item, item) for item in record.source_refs],
            "timestamp": record.created_at,
            "confidence": 1.0 - record.uncertainty,
            "independent": record.independent,
            "transfer": record.transfer,
            "recipe_id": record.recipe_id,
            "recipe_version": record.recipe_version,
        }

    @staticmethod
    def _provider_unavailable(
        events: list[dict[str, Any]], preparation_error: Exception | None
    ) -> bool:
        from deeptutor.services.llm.exceptions import (
            LLMAuthenticationError,
            LLMCircuitBreakerError,
            LLMConfigError,
            LLMModelNotFoundError,
            LLMProviderTransportError,
            LLMRateLimitError,
            LLMTimeoutError,
        )

        provider_failures = (
            LLMConfigError,
            LLMProviderTransportError,
            LLMCircuitBreakerError,
            LLMTimeoutError,
            LLMRateLimitError,
            LLMAuthenticationError,
            LLMModelNotFoundError,
        )
        if isinstance(preparation_error, provider_failures):
            return True
        explicit_codes = {
            "provider_unavailable",
            "provider_transport",
            "llm_config",
            "authentication",
            "rate_limit",
            "model_not_found",
            "circuit_open",
        }
        return any(
            str((event.get("metadata") or {}).get("error_code") or "") in explicit_codes
            for event in events
            if event.get("type") == "error"
        )

    def _store_resume_fixture(
        self,
        store: Any,
        case: EvalCase,
        session_id: str,
        resume_state: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if resume_state is None:
            return None
        from deeptutor.learning.coordinator.models import ActivityPlan
        from deeptutor.learning.models import LearningThread, LearningThreadStatus

        thread_id = str(resume_state.get("thread_id") or "")
        if not thread_id:
            return None
        goal = str(resume_state.get("goal") or case.prompt)
        raw_next_activity = resume_state.get("next_activity")
        if isinstance(raw_next_activity, dict):
            next_activity = ActivityPlan.model_validate(raw_next_activity).model_dump(mode="json")
        else:
            next_activity = ActivityPlan(
                kind="explanation",
                objective=goal,
                learner_action="Continue the persisted learning activity.",
                help_level=int(resume_state.get("help_level") or 0),
            ).model_dump(mode="json")
        thread = store.create_learning_thread(
            LearningThread(
                thread_id=thread_id,
                session_id=session_id,
                scope=case.scope if case.scope in {"lesson", "path"} else "lesson",
                goal=goal,
                status=LearningThreadStatus.ACTIVE,
                next_activity=next_activity,
            )
        )
        return {
            "thread_id": thread.thread_id,
            "next_activity_id": str(
                resume_state.get("next_activity_id")
                or thread.next_activity.get("kind")
                or ""
            ),
            "help_level": int(thread.next_activity.get("help_level") or 0),
            "next_activity": dict(thread.next_activity),
        }

    def _write_raw_output(
        self,
        events: list[dict[str, Any]],
    ) -> str:
        self._raw_output_dir.mkdir(parents=True, exist_ok=True)
        path = self._raw_output_dir / f"{secrets.token_hex(16)}.json"
        write_redacted_report(path, {"events": events})
        return f"{self._raw_output_dir.name}/{path.name}"


def _result_summary(result: EvalResult, score: ContractScore) -> dict[str, Any]:
    return {
        "status": result.status,
        "scope": result.scope,
        "approval_requested": result.approval_requested,
        "path_created_before_approval": result.path_created_before_approval,
        "final_help_level": result.final_help_level,
        "direct_answer_honored": result.direct_answer_honored,
        "source_ids": result.source_ids,
        "evidence": result.evidence,
        "route": result.route,
        "route_available": result.route_available,
        "resumed_from_state": result.resumed_from_state,
        "assessment_valid": result.assessment_valid,
        "mastery_before": result.mastery_before,
        "mastery_after": result.mastery_after,
        "decision_persisted": result.decision_persisted,
        "thread_state": result.thread_state,
        "persisted_evidence_count": result.persisted_evidence_count,
        "activity_consumed": result.activity_consumed,
        "review_material": result.review_material,
        "blocked_reason": result.blocked_reason,
        "deterministic_passed": score.passed,
    }


def _fixture_source_ids(case: EvalCase) -> list[str]:
    return [str(source.get("id") or "") for source in case.sources if source.get("id")]


async def run_paired_evaluation(
    cases: list[EvalCase],
    *,
    runner: Any,
    rubric: dict[str, Any],
    model: str,
    settings: dict[str, Any],
    provider_seed: int,
    seed_supported: bool,
) -> dict[str, Any]:
    """Execute baseline/coordinator pairs against one immutable fixture per case."""

    reviewer_pairs: list[dict[str, Any]] = []
    machine_pairs: list[dict[str, Any]] = []
    any_blocked = False
    any_contract_failure = False
    dimensions = list(rubric["dimensions"])
    for case in cases:
        fixture = {
            "model": model,
            "settings": dict(settings),
            "source_ids": _fixture_source_ids(case),
            "provider_seed": provider_seed,
            "seed_supported": seed_supported,
        }
        resume_state = (
            dict(case.stored_turn_state) if case.interrupted or case.delayed_recall else None
        )
        baseline = await runner.run(case, "off", fixture, resume_state)
        coordinator = await runner.run(case, "active", fixture, resume_state)
        baseline_score = score_contracts(case, baseline)
        coordinator_score = score_contracts(case, coordinator, require_coordinator=True)
        any_blocked = any_blocked or "blocked" in {baseline.status, coordinator.status}
        any_contract_failure = any_contract_failure or not coordinator_score.passed

        named_outputs = {
            "baseline": {
                "raw_output_ref": baseline.raw_output_ref,
                "latency_ms": baseline.latency_ms,
                "token_usage": baseline.token_usage,
            },
            "coordinator": {
                "raw_output_ref": coordinator.raw_output_ref,
                "latency_ms": coordinator.latency_ms,
                "token_usage": coordinator.token_usage,
            },
        }
        randomized_modes = ["baseline", "coordinator"]
        secrets.SystemRandom().shuffle(randomized_modes)
        label_mapping = dict(zip(("A", "B"), randomized_modes, strict=True))
        blinded_outputs = {
            label: {
                "content": (
                    baseline.review_material
                    if mode == "baseline"
                    else coordinator.review_material
                )
            }
            for label, mode in label_mapping.items()
        }
        reviewer_pairs.append(
            {
                "case_id": case.id,
                "case_version": case.version,
                "domain": case.domain,
                "scope": case.scope,
                "blinded_outputs": blinded_outputs,
                "human_rubric": {
                    label: {dimension: None for dimension in dimensions} for label in ("A", "B")
                },
            }
        )
        machine_pairs.append(
            {
                "case_id": case.id,
                "case_version": case.version,
                "domain": case.domain,
                "scope": case.scope,
                "execution_fixture": {
                    "model": model,
                    "settings": dict(settings),
                    "source_ids": _fixture_source_ids(case),
                    "provider_seed": provider_seed,
                    "provider_seed_applied": seed_supported,
                },
                "results": {
                    "baseline": {
                        **named_outputs["baseline"],
                        **_result_summary(baseline, baseline_score),
                    },
                    "coordinator": {
                        **named_outputs["coordinator"],
                        **_result_summary(coordinator, coordinator_score),
                    },
                },
                "contract_failures": {
                    "baseline": baseline_score.failures,
                    "coordinator": coordinator_score.failures,
                },
                "label_mapping": label_mapping,
            }
        )
    release_gate = (
        "blocked_provider"
        if any_blocked
        else "deterministic_failed"
        if any_contract_failure
        else "awaiting_human_review"
    )
    return {
        "reviewer": {
            "schema_version": 2,
            "human_review_required": True,
            "model_grader_used": False,
            "cases": reviewer_pairs,
        },
        "machine": {
            "schema_version": 2,
            "mode": "paired",
            "release_gate": release_gate,
            "human_review_required": True,
            "model_grader_used": False,
            "rubric_dimensions": dimensions,
            "cases": machine_pairs,
        },
    }


def _redact_string(value: str) -> str:
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        value,
    )
    redacted = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
        r"id[_-]?token|token|password|secret|credentials?)\s*[:=]\s*)[^\s,;&#?]+",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", redacted)
    try:
        split = urlsplit(redacted)
    except ValueError:
        return redacted
    if split.scheme not in {"http", "https"} or not split.query:
        return redacted
    query = [
        (key, "[REDACTED]" if key.casefold() in SENSITIVE_QUERY_KEYS else item)
        for key, item in parse_qsl(split.query, keep_blank_values=True)
    ]
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _redacted(value: Any, *, key: str = "") -> Any:
    normalized_key = key.casefold().replace("-", "_")
    if normalized_key in SENSITIVE_KEYS or any(
        normalized_key.endswith(f"_{suffix}")
        for suffix in ("api_key", "token", "password", "secret", "credential", "credentials")
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {item_key: _redacted(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    if isinstance(value, tuple):
        return [_redacted(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def write_redacted_report(path: Path, report: dict[str, Any]) -> None:
    """Serialize a current-user-only report after recursive redaction."""

    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(_redacted(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(serialized)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def machine_artifact_path(reviewer_path: Path) -> Path:
    """Return the stable sealed companion path for a reviewer artifact."""

    return reviewer_path.with_name(f"{reviewer_path.stem}.machine.json")


def unblinded_artifact_path(reviewer_path: Path) -> Path:
    """Return the stable authorized post-review output path."""

    return reviewer_path.with_name(f"{reviewer_path.stem}.unblinded.json")


def write_paired_artifacts(output: Path, artifacts: dict[str, Any]) -> tuple[Path, Path]:
    """Write separate blind-review and sealed machine artifacts."""

    reviewer_path = output
    machine_path = machine_artifact_path(output)
    write_redacted_report(reviewer_path, dict(artifacts["reviewer"]))
    write_redacted_report(machine_path, dict(artifacts["machine"]))
    return reviewer_path, machine_path


def unblind_scored_review(reviewer_path: Path, machine_path: Path) -> dict[str, Any]:
    """Join locked human scores to the authoritative sealed label mapping."""

    reviewer = json.loads(reviewer_path.read_text(encoding="utf-8"))
    machine = json.loads(machine_path.read_text(encoding="utf-8"))
    reviewer_cases = reviewer.get("cases")
    machine_cases = machine.get("cases")
    if not isinstance(reviewer_cases, list) or not isinstance(machine_cases, list):
        raise ValueError("reviewer and machine artifacts must contain case arrays")

    def unique_case_ids(cases: list[Any]) -> list[str]:
        case_ids = [
            str(case.get("case_id") or "")
            for case in cases
            if isinstance(case, dict)
        ]
        if (
            len(case_ids) != len(cases)
            or any(not case_id for case_id in case_ids)
            or len(set(case_ids)) != len(case_ids)
        ):
            raise ValueError("reviewer and machine artifacts must have the same unique case IDs")
        return case_ids

    reviewer_case_ids = unique_case_ids(reviewer_cases)
    machine_case_ids = unique_case_ids(machine_cases)
    if len(reviewer_case_ids) != len(machine_case_ids) or set(reviewer_case_ids) != set(
        machine_case_ids
    ):
        raise ValueError("reviewer and machine artifacts must have the same unique case IDs")
    machine_by_case = {
        str(case.get("case_id") or ""): case
        for case in machine_cases
        if isinstance(case, dict)
    }
    rubric_dimensions = machine.get("rubric_dimensions")
    if not isinstance(rubric_dimensions, list) or not rubric_dimensions:
        raise ValueError("machine artifact does not define rubric dimensions")
    expected_dimensions = {str(dimension) for dimension in rubric_dimensions}

    joined_cases: list[dict[str, Any]] = []
    for reviewer_case in reviewer_cases:
        if not isinstance(reviewer_case, dict):
            raise ValueError("reviewer case is invalid")
        case_id = str(reviewer_case.get("case_id") or "")
        machine_case = machine_by_case.get(case_id)
        if machine_case is None:
            raise ValueError(f"machine artifact is missing case {case_id!r}")
        for field in ("case_version", "domain", "scope"):
            if reviewer_case.get(field) != machine_case.get(field):
                raise ValueError(f"case {case_id!r} does not match machine field {field!r}")
        mapping = machine_case.get("label_mapping")
        named_results = machine_case.get("results")
        blinded_outputs = reviewer_case.get("blinded_outputs")
        human_rubric = reviewer_case.get("human_rubric")
        if not all(
            isinstance(value, dict)
            for value in (mapping, named_results, blinded_outputs, human_rubric)
        ):
            raise ValueError(f"case {case_id!r} has an invalid review contract")
        if set(mapping) != {"A", "B"} or set(mapping.values()) != {
            "baseline",
            "coordinator",
        }:
            raise ValueError(f"case {case_id!r} has an invalid label mapping")

        results: dict[str, Any] = {}
        for label in ("A", "B"):
            mode = str(mapping[label])
            blinded = blinded_outputs.get(label)
            named = named_results.get(mode)
            scores = human_rubric.get(label)
            if not all(isinstance(value, dict) for value in (blinded, named, scores)):
                raise ValueError(f"case {case_id!r} label {label} is incomplete")
            if blinded.get("content") != named.get("review_material"):
                raise ValueError(f"case {case_id!r} label {label} does not round-trip")
            if set(scores) != expected_dimensions or any(
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not 0 <= score <= 4
                for score in scores.values()
            ):
                raise ValueError(f"case {case_id!r} label {label} scores are not locked")
            results[mode] = {**named, "human_rubric": dict(scores)}
        joined_cases.append(
            {
                "case_id": case_id,
                "case_version": reviewer_case.get("case_version"),
                "domain": reviewer_case.get("domain"),
                "scope": reviewer_case.get("scope"),
                "results": results,
            }
        )
    return {
        "schema_version": 2,
        "mode": "unblinded",
        "release_gate": machine.get("release_gate"),
        "human_scores_locked": True,
        "cases": joined_cases,
    }


def _validate_case(case: EvalCase, *, line_number: int) -> None:
    if not case.id or case.version < 1:
        raise ValueError(f"line {line_number}: case id and positive version are required")
    if case.domain not in REQUIRED_DOMAINS:
        raise ValueError(f"line {line_number}: unsupported domain {case.domain!r}")
    if case.scope not in REQUIRED_SCOPES:
        raise ValueError(f"line {line_number}: unsupported scope {case.scope!r}")
    if not case.prompt:
        raise ValueError(f"line {line_number}: prompt is required")
    forbidden = FORBIDDEN_ANSWER_KEYS.intersection(case.expected)
    if forbidden:
        raise ValueError(
            f"line {line_number}: expected must contain contracts, not prose answers: "
            f"{', '.join(sorted(forbidden))}"
        )
    if case.expected.get("scope") != case.scope:
        raise ValueError(f"line {line_number}: expected scope must match fixture scope")
    if case.interrupted and not case.stored_turn_state:
        raise ValueError(f"line {line_number}: interrupted case needs stored_turn_state")
    if case.delayed_recall:
        if not case.follow_up_prompt.strip() or not case.stored_turn_state:
            raise ValueError(
                f"line {line_number}: delayed recall needs a follow-up prompt and stored state"
            )
        from deeptutor.learning.coordinator.models import ActivityPlan

        activity = ActivityPlan.model_validate(case.stored_turn_state.get("next_activity"))
        if activity.assessment_method != "delayed_retrieval" or not activity.independent_required:
            raise ValueError(
                f"line {line_number}: delayed recall needs an independent delayed-retrieval activity"
            )


def load_cases(path: Path) -> list[EvalCase]:
    """Load and validate a versioned JSONL scenario matrix."""

    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            payload = json.loads(raw_line)
            if not isinstance(payload, dict):
                raise ValueError(f"line {line_number}: case must be a JSON object")
            case = EvalCase(payload)
            _validate_case(case, line_number=line_number)
            if case.id in seen_ids:
                raise ValueError(f"line {line_number}: duplicate case id {case.id!r}")
            seen_ids.add(case.id)
            cases.append(case)
    return cases


def validate_scenario_matrix(cases: list[EvalCase]) -> None:
    """Reject incomplete release matrices before any provider calls run."""

    if len(cases) < 40:
        raise ValueError("scenario matrix requires at least 40 cases")
    domains = {case.domain for case in cases}
    missing_domains = REQUIRED_DOMAINS - domains
    if missing_domains:
        raise ValueError(f"scenario matrix missing domains: {', '.join(sorted(missing_domains))}")
    required_states = (
        "misconception",
        "stuck_signal",
        "direct_answer",
        "attached_only",
        "missing_tools",
        "interrupted",
        "delayed_recall",
    )
    for domain in sorted(REQUIRED_DOMAINS):
        domain_cases = [case for case in cases if case.domain == domain]
        scopes = {case.scope for case in domain_cases}
        if scopes != REQUIRED_SCOPES:
            missing_scopes = REQUIRED_SCOPES - scopes
            raise ValueError(
                f"{domain} missing scopes: {', '.join(sorted(missing_scopes)) or 'unknown'}"
            )
        for state in required_states:
            if not any(getattr(case, state) for case in domain_cases):
                raise ValueError(f"{domain} missing required state: {state}")


def load_rubric(path: Path) -> dict[str, Any]:
    """Load the human-review rubric used by paired report generation."""

    with path.open(encoding="utf-8") as stream:
        rubric = yaml.safe_load(stream)
    if not isinstance(rubric, dict):
        raise ValueError("rubric must be a mapping")
    return rubric


def _valid_evidence_schema(value: dict[str, Any]) -> bool:
    required_nonempty_text = ("objective_id", "activity_id", "learner_response_ref", "outcome")
    if any(
        not isinstance(value.get(field), str) or not value[field].strip()
        for field in required_nonempty_text
    ):
        return False
    if value["outcome"] not in {"correct", "partial", "incorrect", "unassessed"}:
        return False
    rubric = value.get("rubric")
    if not isinstance(rubric, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not item["id"].strip()
        or not isinstance(item.get("passed"), bool)
        for item in rubric
    ):
        return False
    help_level = value.get("help_level")
    if not isinstance(help_level, int) or isinstance(help_level, bool) or not 0 <= help_level <= 4:
        return False
    if not isinstance(value.get("source_refs"), list) or any(
        not isinstance(item, str) or not item.strip() for item in value["source_refs"]
    ):
        return False
    timestamp = value.get("timestamp")
    confidence = value.get("confidence")
    return (
        isinstance(timestamp, (int, float))
        and not isinstance(timestamp, bool)
        and timestamp >= 0
        and isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and 0 <= confidence <= 1
        and isinstance(value.get("independent"), bool)
    )


def score_contracts(
    case: EvalCase,
    result: EvalResult,
    *,
    require_coordinator: bool = False,
) -> ContractScore:
    """Score runtime-owned learning contracts without an LLM judge."""

    failures: list[str] = []
    if result.status == "blocked":
        failures.append("provider_unavailable")
    elif result.status != "completed":
        failures.append("execution_failed")
    if result.scope != case.expected["scope"]:
        failures.append("scope_mismatch")
    if result.approval_requested is not bool(case.expected["requires_approval"]):
        failures.append("approval_gate_mismatch")
    if result.path_created_before_approval:
        failures.append("path_created_before_approval")
    if (
        case.direct_answer
        and result.status == "completed"
        and result.direct_answer_honored is not True
    ):
        failures.append("direct_answer_not_honored")
    allowed_source_ids = set(case.expected.get("allowed_source_ids", []))
    observed_source_ids = set(result.source_ids)
    if result.evidence is not None and isinstance(result.evidence.get("source_refs"), list):
        observed_source_ids.update(result.evidence["source_refs"])
    if case.attached_only and not observed_source_ids.issubset(allowed_source_ids):
        failures.append("untrusted_source_ids")
    if result.evidence is not None and not _valid_evidence_schema(result.evidence):
        failures.append("invalid_evidence_schema")
    if (
        result.evidence is not None
        and result.evidence.get("help_level") == 4
        and result.evidence.get("independent") is True
    ):
        failures.append("complete_answer_marked_independent")
    minimum_help_level = case.expected.get("minimum_help_level")
    if minimum_help_level is not None and (
        result.final_help_level is None or result.final_help_level < int(minimum_help_level)
    ):
        failures.append("help_level_too_low")
    if result.route_available is not bool(case.expected["route_available"]):
        failures.append("route_availability_mismatch")
    expected_resume = case.expected.get("resume_state")
    if isinstance(expected_resume, dict) and any(
        result.resumed_from_state.get(key) != expected for key, expected in expected_resume.items()
    ):
        failures.append("resume_state_mismatch")
    if result.assessment_valid is False and result.mastery_before != result.mastery_after:
        failures.append("invalid_assessment_mutated_mastery")
    if require_coordinator and result.status == "completed":
        if not result.decision_persisted:
            failures.append("coordinator_decision_not_persisted")
        if case.scope in {"lesson", "path"} and not result.thread_state:
            failures.append("learning_thread_not_persisted")
        if (case.interrupted or case.delayed_recall) and not result.activity_consumed:
            failures.append("resume_activity_not_consumed")
        if case.delayed_recall and result.evidence is None:
            failures.append("delayed_recall_not_observed")
    return ContractScore(failures)


def _provider_details() -> tuple[str, dict[str, Any], bool]:
    from deeptutor.services.config import get_chat_params
    from deeptutor.services.llm.config import get_llm_config
    from deeptutor.services.provider_registry import effective_backend, find_by_name

    config = get_llm_config()
    provider_name = str(config.provider_name or config.binding or "")
    spec = find_by_name(provider_name)
    backend = effective_backend(spec, config.api_format)
    seed_supported = backend in {"openai_compat", "azure_openai"}
    chat = get_chat_params()
    settings = {
        "temperature": chat.get("temperature"),
        "max_rounds": chat.get("max_rounds"),
        "exploring": dict(chat.get("exploring") or {}),
        "responding": dict(chat.get("responding") or {}),
        "tools": [],
    }
    return str(config.model or ""), settings, seed_supported


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("paired", "unblind"), default="paired")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider-seed", type=int, default=DEFAULT_PROVIDER_SEED)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Do not contact a provider; emit explicit blocked records.",
    )
    return parser.parse_args(argv)


async def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    if args.mode == "unblind":
        machine_path = machine_artifact_path(args.output)
        report = unblind_scored_review(args.output, machine_path)
        target = unblinded_artifact_path(args.output)
        write_redacted_report(target, report)
        return {"unblinded": report, "output": str(target)}

    cases = load_cases(args.cases)
    validate_scenario_matrix(cases)
    rubric = load_rubric(args.rubric)
    if args.offline:
        runner: Any = UnavailableProviderRunner("provider_unavailable: offline run requested")
        model = ""
        settings: dict[str, Any] = {}
        seed_supported = False
        report = await run_paired_evaluation(
            cases,
            runner=runner,
            rubric=rubric,
            model=model,
            settings=settings,
            provider_seed=args.provider_seed,
            seed_supported=seed_supported,
        )
    else:
        try:
            model, settings, seed_supported = _provider_details()
        except Exception as exc:
            runner = UnavailableProviderRunner(f"provider_unavailable: {type(exc).__name__}: {exc}")
            model = ""
            settings = {}
            seed_supported = False
            report = await run_paired_evaluation(
                cases,
                runner=runner,
                rubric=rubric,
                model=model,
                settings=settings,
                provider_seed=args.provider_seed,
                seed_supported=seed_supported,
            )
        else:
            raw_output_dir = args.output.parent / f".{secrets.token_hex(16)}.raw"
            with tempfile.TemporaryDirectory(prefix="deeptutor-learning-eval-") as temporary:
                runner = LocalDeepTutorRunner(
                    runtime_root=Path(temporary),
                    raw_output_dir=raw_output_dir,
                    seed_supported=seed_supported,
                )
                report = await run_paired_evaluation(
                    cases,
                    runner=runner,
                    rubric=rubric,
                    model=model,
                    settings=settings,
                    provider_seed=args.provider_seed,
                    seed_supported=seed_supported,
                )
    write_paired_artifacts(args.output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = asyncio.run(_run_cli(args))
    if args.mode == "unblind":
        unblinded = report["unblinded"]
        print(
            f"wrote {len(unblinded['cases'])} unblinded cases to {report['output']} "
            f"(release_gate={unblinded['release_gate']})"
        )
        return 0
    machine = report["machine"]
    blocked = sum(
        1
        for pair in machine["cases"]
        if "blocked"
        in {
            pair["results"]["baseline"]["status"],
            pair["results"]["coordinator"]["status"],
        }
    )
    print(
        f"wrote {len(machine['cases'])} paired cases to {args.output} and "
        f"{machine_artifact_path(args.output)} "
        f"(release_gate={machine['release_gate']}, blocked={blocked})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
