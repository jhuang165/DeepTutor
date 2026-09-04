#!/usr/bin/env python3
"""Paired teaching-quality evaluation for the Learning Coordinator."""

from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
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


def _source_manifest(case: EvalCase) -> tuple[list[Any], str, dict[str, str]]:
    from deeptutor.core.context import Attachment

    attachments: list[Attachment] = []
    lines: list[str] = []
    source_index: dict[str, str] = {}
    for source in case.sources:
        source_id = str(source.get("id") or "").strip()
        text = str(source.get("text") or "")
        filename = str(source.get("filename") or source_id)
        if not source_id:
            continue
        attachments.append(
            Attachment(
                type="file",
                filename=filename,
                mime_type="text/plain",
                id=source_id,
                base64=base64.b64encode(text.encode("utf-8")).decode("ascii"),
                extracted_text=text,
            )
        )
        source_index[source_id] = text
        lines.append(f"- id={source_id} name={filename} type=file preview={text[:240]}")
    manifest = "[Attached Sources]\n" + "\n".join(lines) if lines else ""
    return attachments, manifest, source_index


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


def _response_gives_direct_answer(response: str) -> bool:
    """Return whether the executed terminal response contains a declarative answer."""

    normalized = re.sub(r"[`*_>#]", " ", response).strip()
    if not normalized:
        return False
    clauses = [item.strip() for item in re.split(r"(?<=[.!?])\s+|[\r\n]+", normalized)]
    return any(
        clause
        and not clause.endswith("?")
        and bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", clause))
        for clause in clauses
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
    """Run isolated ordinary-chat and active-coordinator turns in process."""

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
    def _isolated_learning_store(self, store: Any):
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
            yield

    async def run(
        self,
        case: EvalCase,
        coordinator_mode: str,
        fixture: dict[str, Any],
        resume_state: dict[str, Any] | None,
    ) -> EvalResult:
        from deeptutor.core.context import TurnRuntimeContext, UnifiedContext
        from deeptutor.learning.coordinator import LearningCoordinator, decision_payload
        from deeptutor.learning.coordinator.models import CapabilityLearningResult
        from deeptutor.learning.evidence import validate_open_assessment
        from deeptutor.learning.storage import LearningStore
        from deeptutor.runtime.orchestrator import ChatOrchestrator
        from deeptutor.runtime.registry.capability_registry import get_capability_registry
        from deeptutor.runtime.registry.tool_registry import get_tool_registry
        from deeptutor.services.llm.config import get_llm_config

        started = time.perf_counter()
        arm_root = self._runtime_root / f"arm-{secrets.token_hex(16)}"
        store = LearningStore(root=arm_root / "learning")
        attachments, source_manifest, source_index = _source_manifest(case)
        registry = get_capability_registry()
        tool_registry = get_tool_registry()
        session_id = (
            "eval-"
            + hashlib.sha256(case.id.encode("utf-8")).hexdigest()[:24]
        )
        turn_id = "eval-turn-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:20]
        loaded_resume = self._store_resume_fixture(
            store,
            case,
            session_id,
            resume_state,
        )
        extension_state: dict[str, dict[str, Any]] = {}
        active_capability = "chat"
        decision = None
        payload = {
            "content": case.prompt,
            "capability": "chat",
            "language": "en",
            "tools": [],
            "attachments": [dict(source) for source in case.sources],
            "knowledge_bases": [],
            "learning_state": {
                "previous_help_level": int((loaded_resume or {}).get("help_level", 0)),
                "last_outcome": str((loaded_resume or {}).get("last_outcome", "")),
                "repeated_request": bool(loaded_resume),
                "server_next_activity": None,
            },
        }
        before = _mastery_snapshot(store)
        events: list[dict[str, Any]] = []
        context = None
        preparation_error: Exception | None = None
        try:
            if coordinator_mode == "active":
                decision = await LearningCoordinator(store=store).prepare_payload(
                    payload,
                    set(registry.list_capabilities()),
                    get_llm_config(),
                )
                decision = decision.model_copy(
                    update={
                        "thread_id": str((loaded_resume or {}).get("thread_id") or "")
                        or "eval-thread-"
                        + hashlib.sha256(case.id.encode("utf-8")).hexdigest()[:20]
                    }
                )
                active_capability = (
                    decision.route if registry.get(decision.route) is not None else "chat"
                )
                extension_state = {
                    "learning_coordinator": {"decision": decision_payload(decision)}
                }

            context = UnifiedContext(
                session_id=session_id,
                user_message=case.prompt,
                conversation_history=[],
                enabled_tools=[],
                allowed_builtin_tools=[],
                active_capability=active_capability,
                attachments=attachments,
                source_manifest=source_manifest,
                runtime=TurnRuntimeContext(turn_id=turn_id),
                extension_state=extension_state,
                metadata={
                    "turn_id": turn_id,
                    "source_index": source_index,
                    "book_references": [],
                    "mastery_path_id": "",
                    "_mastery_nav_available": False,
                },
            )
            with (
                self._isolated_learning_store(store),
                _provider_seed(int(fixture["provider_seed"]), supported=self._seed_supported),
            ):
                async for event in ChatOrchestrator(registry).handle(context):
                    events.append(event.to_dict())
        except Exception as exc:
            preparation_error = exc
            metadata: dict[str, Any] = {}
            error_code = getattr(exc, "error_code", None)
            if isinstance(error_code, str) and error_code:
                metadata["error_code"] = error_code
            events.append({"type": "error", "content": str(exc), "metadata": metadata})
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
        coordinator_result = (
            context.extension_state.get("learning_coordinator", {}).get("result")
            if context is not None
            else None
        )
        assessment_valid: bool | None = None
        evidence = None
        if isinstance(coordinator_result, dict):
            parsed = CapabilityLearningResult.model_validate(coordinator_result)
            if parsed.assessment is not None:
                validated = validate_open_assessment(parsed.assessment, case.prompt)
                assessment_valid = validated is not None
                if validated is not None and decision is not None:
                    evidence = {
                        "objective_id": decision.objective_id or decision.thread_id,
                        "activity_id": decision.activity.kind.value,
                        "learner_response_ref": f"chat-turn:{turn_id}:user",
                        "rubric": [item.model_dump(mode="json") for item in validated.rubric],
                        "outcome": validated.outcome,
                        "help_level": decision.activity.help_level,
                        "source_refs": parsed.source_refs,
                        "timestamp": time.time(),
                        "confidence": 1.0 - validated.uncertainty,
                        "independent": bool(decision.activity.independent_required),
                    }
        citation_ids = set()
        for item in events:
            metadata = item.get("metadata") or {}
            citation_ids.update(_citation_ids(metadata.get("citations")))
            citation_ids.update(_citation_ids(metadata.get("citation_ids")))
        planned_route = decision.route if decision is not None else active_capability
        route_available = registry.get(planned_route) is not None and all(
            tool_registry.get(name) is not None for name in case.missing_tools
        )
        help_level = decision.activity.help_level if decision is not None else 0
        response = str(result_metadata.get("response") or "")
        return EvalResult(
            status=status,
            scope=decision.scope.value if decision is not None else case.scope,
            approval_requested=(decision.requires_approval if decision is not None else False),
            path_created_before_approval=(case.scope == "path" and before != after),
            final_help_level=help_level,
            direct_answer_honored=(
                _response_gives_direct_answer(response) if case.direct_answer else None
            ),
            source_ids=sorted(citation_ids),
            evidence=evidence,
            route=active_capability,
            route_available=route_available,
            resumed_from_state=self._observed_resume_state(store, loaded_resume),
            assessment_valid=assessment_valid,
            mastery_before=before,
            mastery_after=after,
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
        from deeptutor.learning.models import LearningThread, LearningThreadStatus

        thread_id = str(resume_state.get("thread_id") or "")
        if not thread_id:
            return None
        next_activity = {
            "activity_id": str(resume_state.get("next_activity_id") or ""),
            "help_level": int(resume_state.get("help_level") or 0),
        }
        if resume_state.get("last_outcome"):
            next_activity["last_outcome"] = str(resume_state["last_outcome"])
        store.create_learning_thread(
            LearningThread(
                thread_id=thread_id,
                session_id=session_id,
                scope=case.scope if case.scope in {"lesson", "path"} else "lesson",
                goal=case.prompt,
                status=LearningThreadStatus.ACTIVE,
                next_activity=next_activity,
            )
        )
        return self._observed_resume_state(store, {"thread_id": thread_id})

    @staticmethod
    def _observed_resume_state(
        store: Any, loaded_resume: dict[str, Any] | None
    ) -> dict[str, Any]:
        if not loaded_resume:
            return {}
        thread = store.get_learning_thread(str(loaded_resume.get("thread_id") or ""))
        if thread is None:
            return {}
        return {
            "thread_id": thread.thread_id,
            "next_activity_id": str(thread.next_activity.get("activity_id") or ""),
            "help_level": int(thread.next_activity.get("help_level") or 0),
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

    pairs: list[dict[str, Any]] = []
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
        resume_state = dict(case.stored_turn_state) if case.interrupted else None
        baseline = await runner.run(case, "off", fixture, resume_state)
        coordinator = await runner.run(case, "active", fixture, resume_state)
        baseline_score = score_contracts(case, baseline)
        coordinator_score = score_contracts(case, coordinator)
        any_blocked = any_blocked or "blocked" in {baseline.status, coordinator.status}
        any_contract_failure = any_contract_failure or not coordinator_score.passed

        outputs = [
            {
                "raw_output_ref": baseline.raw_output_ref,
                "latency_ms": baseline.latency_ms,
                "token_usage": baseline.token_usage,
            },
            {
                "raw_output_ref": coordinator.raw_output_ref,
                "latency_ms": coordinator.latency_ms,
                "token_usage": coordinator.token_usage,
            },
        ]
        secrets.SystemRandom().shuffle(outputs)
        pairs.append(
            {
                "case_id": case.id,
                "case_version": case.version,
                "domain": case.domain,
                "scope": case.scope,
                "execution_fixture": {
                    "model": model,
                    "settings": dict(settings),
                    "source_ids": _fixture_source_ids(case),
                    "provider_seed_applied": seed_supported,
                },
                "baseline_result": _result_summary(baseline, baseline_score),
                "coordinator_result": _result_summary(coordinator, coordinator_score),
                "contract_failures": {
                    "baseline": baseline_score.failures,
                    "coordinator": coordinator_score.failures,
                },
                "blinded_outputs": {"A": outputs[0], "B": outputs[1]},
                "human_rubric": {
                    label: {dimension: None for dimension in dimensions} for label in ("A", "B")
                },
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
        "schema_version": 1,
        "mode": "paired",
        "release_gate": release_gate,
        "human_review_required": True,
        "model_grader_used": False,
        "cases": pairs,
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
    """Serialize a report after recursive credential and URL-query redaction."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_redacted(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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


def score_contracts(case: EvalCase, result: EvalResult) -> ContractScore:
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
    parser.add_argument("--mode", choices=("paired",), default="paired")
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
            raw_output_dir = args.output.parent / f"{args.output.stem}.raw"
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
    write_redacted_report(args.output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = asyncio.run(_run_cli(args))
    blocked = sum(
        1
        for pair in report["cases"]
        if "blocked"
        in {
            pair["baseline_result"]["status"],
            pair["coordinator_result"]["status"],
        }
    )
    print(
        f"wrote {len(report['cases'])} paired cases to {args.output} "
        f"(release_gate={report['release_gate']}, blocked={blocked})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
