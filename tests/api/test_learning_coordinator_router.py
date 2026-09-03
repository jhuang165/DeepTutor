from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib
import sqlite3
import threading
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import pytest

from deeptutor.learning.models import EvidenceRecord, LearningThread
from deeptutor.learning.storage import LearningStore
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope


def _router_module():
    return importlib.import_module("deeptutor.api.routers.learning_coordinator")


@pytest.fixture
def users(tmp_path, monkeypatch: pytest.MonkeyPatch) -> dict[str, CurrentUser]:
    from deeptutor.multi_user import paths

    monkeypatch.setattr(paths, "_path_services", {})
    return {
        name: CurrentUser(
            id=f"u_{name}",
            username=name,
            role="user",
            scope=UserScope(
                kind="user",
                user_id=f"u_{name}",
                root=(tmp_path / name).resolve(),
            ),
        )
        for name in ("alice", "bob")
    }


@contextmanager
def _as_user(user: CurrentUser):
    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)


@pytest.fixture
def client(users: dict[str, CurrentUser]) -> TestClient:
    async def require_test_user(authorization: str | None = Header(default=None)):
        scheme, _, name = str(authorization or "").partition(" ")
        user = users.get(name) if scheme == "Bearer" else None
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        token = set_current_user(user)
        try:
            yield user
        finally:
            reset_current_user(token)

    app = FastAPI()
    app.include_router(
        _router_module().router,
        prefix="/api/learning",
        dependencies=[Depends(require_test_user)],
    )
    return TestClient(app)


def _headers(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def _thread(
    *,
    thread_id: str = "thread-1",
    session_id: str = "session-1",
    scope: str = "lesson",
    status: str = "active",
    goal: str = "Understand eigenvectors",
    next_activity: dict[str, Any] | None = None,
) -> LearningThread:
    return LearningThread(
        thread_id=thread_id,
        session_id=session_id,
        scope=scope,
        goal=goal,
        status=status,
        next_activity=next_activity
        or {
            "kind": "prediction",
            "objective": goal,
            "learner_action": "Predict what happens.",
            "help_level": 0,
            "recipe_step": 0,
        },
    )


def _approval_body() -> dict[str, Any]:
    return {
        "name": "Linear Algebra",
        "goal": "Understand eigenvectors",
        "description": "A short route through invariant directions.",
        "emoji": "🧭",
        "sources": [],
        "modules": [
            {
                "name": "Eigenvectors",
                "knowledge_points": [{"name": "Invariant directions", "type": "concept"}],
            }
        ],
    }


def test_real_app_mount_rejects_unauthenticated_learning_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeptutor.api import main as api_main
    from deeptutor.api.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)

    response = TestClient(api_main.app).get("/api/learning/queue")

    assert response.status_code == 401


def test_learning_threads_are_isolated_per_user(
    client: TestClient,
    users: dict[str, CurrentUser],
) -> None:
    for name, goal in (("alice", "Alice goal"), ("bob", "Bob goal")):
        with _as_user(users[name]):
            LearningStore().create_learning_thread(_thread(goal=goal))

    assert (
        client.get("/api/learning/threads/thread-1", headers=_headers("alice")).json()["thread"][
            "goal"
        ]
        == "Alice goal"
    )
    assert (
        client.get("/api/learning/threads/thread-1", headers=_headers("bob")).json()["thread"][
            "goal"
        ]
        == "Bob goal"
    )


def test_queue_response_has_typed_items_shape(
    client: TestClient,
    users: dict[str, CurrentUser],
) -> None:
    with _as_user(users["alice"]):
        LearningStore().create_learning_thread(_thread())

    response = client.get(
        "/api/learning/queue?session_id=session-1",
        headers=_headers("alice"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert payload["items"][0]["thread_id"] == "thread-1"
    assert payload["items"][0]["reason"] == "resume_lesson"


def test_thread_not_found_returns_404(client: TestClient) -> None:
    response = client.get(
        "/api/learning/threads/missing-thread",
        headers=_headers("alice"),
    )

    assert response.status_code == 404


def test_evidence_removal_is_idempotent_and_returns_revised_mastery(
    client: TestClient,
    users: dict[str, CurrentUser],
) -> None:
    with _as_user(users["alice"]):
        store = LearningStore()
        store.create_learning_thread(_thread(thread_id="thread-path", scope="path", status="draft"))

    approved = client.post(
        "/api/learning/threads/thread-path/approve-path",
        headers=_headers("alice"),
        json=_approval_body(),
    ).json()
    path_id = approved["path_id"]
    objective_id = f"{path_id}_m0_kp0"
    with _as_user(users["alice"]):
        store = LearningStore()
        record = EvidenceRecord(
            evidence_id="evidence-1",
            thread_id="thread-path",
            path_id=path_id,
            objective_id=objective_id,
            activity_kind="teach_back",
            recipe_id="concept-transfer",
            recipe_version=1,
            response="It remains on the same line.",
            outcome="correct",
            help_level=0,
            independent=True,
            transfer=True,
            session_id="session-1",
            turn_id="turn-1",
        )
        store.append_evidence(record)

    first = client.delete("/api/learning/evidence/evidence-1", headers=_headers("alice"))
    replay = client.delete("/api/learning/evidence/evidence-1", headers=_headers("alice"))

    assert first.status_code == replay.status_code == 200
    assert first.json()["evidence"] == replay.json()["evidence"] == []
    assert first.json()["mastery_revision"] >= 1
    assert replay.json()["mastery_revision"] >= first.json()["mastery_revision"]
    with _as_user(users["alice"]):
        removed = LearningStore().get_evidence("evidence-1")
        assert removed is not None and removed.removed_at is not None


def test_help_level_is_validated_and_only_increases_current_activity(
    client: TestClient,
    users: dict[str, CurrentUser],
) -> None:
    with _as_user(users["alice"]):
        LearningStore().create_learning_thread(_thread())

    invalid = client.post(
        "/api/learning/threads/thread-1/help",
        headers=_headers("alice"),
        json={"help_level": 5},
    )
    increased = client.post(
        "/api/learning/threads/thread-1/help",
        headers=_headers("alice"),
        json={"help_level": 2},
    )
    decreased = client.post(
        "/api/learning/threads/thread-1/help",
        headers=_headers("alice"),
        json={"help_level": 1},
    )

    assert invalid.status_code == 422
    assert increased.status_code == 200
    assert increased.json()["thread"]["next_activity"]["help_level"] == 2
    assert decreased.status_code == 409


def test_path_approval_creates_and_binds_once(
    client: TestClient,
    users: dict[str, CurrentUser],
) -> None:
    with _as_user(users["alice"]):
        LearningStore().create_learning_thread(
            _thread(thread_id="thread-path", scope="path", status="draft")
        )

    first = client.post(
        "/api/learning/threads/thread-path/approve-path",
        headers=_headers("alice"),
        json=_approval_body(),
    )
    replay = client.post(
        "/api/learning/threads/thread-path/approve-path",
        headers=_headers("alice"),
        json=_approval_body(),
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["path_id"] == replay.json()["path_id"]
    with _as_user(users["alice"]):
        store = LearningStore()
        thread = store.get_learning_thread("thread-path")
        assert thread is not None
        assert thread.path_id == first.json()["path_id"]
        assert thread.status.value == "active"
        assert store.load(thread.path_id) is not None
        assert store.get_topic(thread.path_id) is not None


def test_concurrent_path_approval_uses_one_atomic_identity(tmp_path) -> None:
    from deeptutor.learning.topic_generation import materialize_topic_draft

    store = LearningStore(root=tmp_path)
    thread = store.create_learning_thread(
        _thread(thread_id="thread-path", scope="path", status="draft")
    )
    path_id = (
        "topic_" + __import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, thread.thread_id).hex
    )
    materialized = materialize_topic_draft(path_id=path_id, **_approval_body())
    barrier = threading.Barrier(2)

    def approve() -> str:
        barrier.wait()
        return store.approve_learning_thread_path(thread.thread_id, materialized)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: approve(), range(2)))

    assert results == [path_id, path_id]
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM mastery_paths").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM mastery_topic_meta").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM learning_audit_events "
                "WHERE event_type = 'thread.path_approved'"
            ).fetchone()[0]
            == 1
        )


def test_path_approval_rolls_back_thread_claim_when_topic_write_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeptutor.learning.storage import LearningTransaction
    from deeptutor.learning.topic_generation import materialize_topic_draft

    store = LearningStore(root=tmp_path)
    thread = store.create_learning_thread(
        _thread(thread_id="thread-path", scope="path", status="draft")
    )
    path_id = (
        "topic_" + __import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, thread.thread_id).hex
    )
    materialized = materialize_topic_draft(path_id=path_id, **_approval_body())

    def fail_topic_write(*_args, **_kwargs) -> None:
        raise RuntimeError("simulated topic write failure")

    monkeypatch.setattr(LearningTransaction, "put_topic", fail_topic_write)

    with pytest.raises(RuntimeError, match="simulated topic write failure"):
        store.approve_learning_thread_path(thread.thread_id, materialized)

    rolled_back = store.get_learning_thread(thread.thread_id)
    assert rolled_back is not None
    assert rolled_back.path_id == ""
    assert rolled_back.status.value == "draft"
    assert store.list_all() == []
