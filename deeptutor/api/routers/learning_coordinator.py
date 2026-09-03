"""Authenticated Learning Coordinator state and action API."""

from __future__ import annotations

import asyncio
from typing import Any
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deeptutor.api.routers.mastery_path import ConfirmTopicRequest, _topic_sources
from deeptutor.learning.coordinator.models import LearningQueueItem
from deeptutor.learning.models import EvidenceRecord, LearningThread, LearningThreadStatus
from deeptutor.learning.queue import LearningQueueService
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore, LearningStoreError
from deeptutor.learning.topic_generation import TopicGenerationError, materialize_topic_draft

router = APIRouter()


class LearningQueueResponse(BaseModel):
    items: list[LearningQueueItem]


class LearningThreadResponse(BaseModel):
    thread: LearningThread


class EvidenceListResponse(BaseModel):
    evidence: list[EvidenceRecord]
    mastery_revision: int | None = None


class ApprovePathResponse(BaseModel):
    path_id: str
    thread: LearningThread


class LearningHelpRequest(BaseModel):
    help_level: int = Field(ge=0, le=4)


def _thread_or_404(store: LearningStore, thread_id: str) -> LearningThread:
    thread = store.get_learning_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Learning thread not found")
    return thread


@router.get("/queue", response_model=LearningQueueResponse)
async def get_queue(session_id: str = "") -> LearningQueueResponse:
    items = await asyncio.to_thread(LearningQueueService().list_items, session_id=session_id)
    return LearningQueueResponse(items=items)


@router.get("/threads/{thread_id}", response_model=LearningThreadResponse)
async def get_thread(thread_id: str) -> LearningThreadResponse:
    thread = await asyncio.to_thread(_thread_or_404, LearningStore(), thread_id)
    return LearningThreadResponse(thread=thread)


@router.get("/threads/{thread_id}/evidence", response_model=EvidenceListResponse)
async def get_thread_evidence(thread_id: str) -> EvidenceListResponse:
    store = LearningStore()
    await asyncio.to_thread(_thread_or_404, store, thread_id)
    evidence = await asyncio.to_thread(store.list_evidence, thread_id=thread_id)
    return EvidenceListResponse(evidence=evidence)


@router.delete("/evidence/{evidence_id}", response_model=EvidenceListResponse)
async def delete_evidence(evidence_id: str) -> EvidenceListResponse:
    store = LearningStore()
    existing = await asyncio.to_thread(store.get_evidence, evidence_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Learning evidence not found")
    removed = await asyncio.to_thread(LearningService(store).remove_evidence, evidence_id)
    if removed is None:  # pragma: no cover - row was read above
        raise HTTPException(status_code=404, detail="Learning evidence not found")
    evidence = await asyncio.to_thread(store.list_evidence, thread_id=removed.thread_id)
    mastery_revision = None
    if removed.path_id:
        progress = await asyncio.to_thread(store.load, removed.path_id)
        mastery_revision = progress.version if progress is not None else None
    return EvidenceListResponse(evidence=evidence, mastery_revision=mastery_revision)


@router.post("/threads/{thread_id}/approve-path", response_model=ApprovePathResponse)
async def approve_path(
    thread_id: str,
    body: ConfirmTopicRequest,
) -> ApprovePathResponse:
    store = LearningStore()
    thread = await asyncio.to_thread(_thread_or_404, store, thread_id)
    path_id = f"topic_{uuid.uuid5(uuid.NAMESPACE_URL, thread.thread_id).hex}"
    try:
        materialized = materialize_topic_draft(
            path_id=path_id,
            name=body.name,
            goal=body.goal,
            description=body.description,
            emoji=body.emoji,
            sources=_topic_sources(body.sources),
            modules=body.modules,
        )
        path_id = await asyncio.to_thread(
            store.approve_learning_thread_path,
            thread_id,
            materialized,
        )
    except TopicGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LearningStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = await asyncio.to_thread(_thread_or_404, store, thread_id)
    return ApprovePathResponse(path_id=path_id, thread=updated)


@router.post("/threads/{thread_id}/help", response_model=LearningThreadResponse)
async def set_help_level(
    thread_id: str,
    body: LearningHelpRequest,
) -> LearningThreadResponse:
    store = LearningStore()
    thread = await asyncio.to_thread(_thread_or_404, store, thread_id)
    if thread.status is not LearningThreadStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Learning thread is not active")
    activity: dict[str, Any] = dict(thread.next_activity)
    if not activity:
        raise HTTPException(status_code=409, detail="Learning thread has no current activity")
    try:
        current_level = int(activity.get("help_level") or 0)
    except (TypeError, ValueError):
        current_level = 0
    if body.help_level == 0:
        activity["recipe_step"] = int(activity.get("recipe_step") or 0) + 1
    elif body.help_level <= current_level:
        raise HTTPException(status_code=409, detail="Help level may only increase")
    activity["help_level"] = body.help_level
    updated = await asyncio.to_thread(
        store.set_learning_thread_next_activity,
        thread_id,
        activity,
    )
    return LearningThreadResponse(thread=updated)
