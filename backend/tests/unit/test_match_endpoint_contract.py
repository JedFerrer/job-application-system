"""Endpoint contract tests for /matches cursor compatibility."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_match_service, get_task_store
from app.api.v1 import matches as matches_module
from app.core.lifespan import register_exception_handlers
from app.models.user import User

pytestmark = pytest.mark.unit


def _build_app_with_overrides(*, current_user: User, match_service: object) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(matches_module.router)

    async def _override_get_current_user() -> User:
        return current_user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_match_service] = lambda: match_service
    return app


class _FailingMatchService:
    async def list_matches(self, **kwargs: object) -> object:  # noqa: ANN001
        assert kwargs["cursor"] == "bad-cursor"
        raise ValueError("bad cursor")


class _EmptyMatchService:
    async def list_matches(self, **kwargs: object) -> object:  # noqa: ANN001
        return type(
            "Resp",
            (),
            {
                "items": [],
                "total": 0,
                "limit": kwargs["limit"],
                "offset": 0 if kwargs.get("offset") is None else kwargs["offset"],
                "next_cursor": None,
                "has_more": False,
            },
        )()


class _SummaryMatchService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def get_match_summary(self, *, user_id: int, job_id: int) -> None:
        self.calls.append((user_id, job_id))
        return None


class _EvidenceMatchService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def get_match_evidence(self, *, user_id: int, job_id: int) -> object:  # noqa: ANN201
        self.calls.append((user_id, job_id))
        return {
            "job_id": job_id,
            "skills": {},
            "experience_requirements": [],
        }


class _AnalyzeMatchService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def analyze_job(self, *, user_id: int, job_id: int) -> object:  # noqa: ANN201
        self.calls.append((user_id, job_id))
        return {
            "job_id": job_id,
            "vector_score": 72.5,
            "confidence": "moderate",
            "llm_score": None,
            "matched_skills": [],
            "missing_keywords": [],
            "readiness": {
                "decision": "save",
                "reasons": [],
                "caveats": [],
                "deterministic_blockers": [],
                "deterministic_warnings": [],
                "decision_basis": "score",
            },
        }


def test_invalid_cursor_returns_400() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_FailingMatchService())
    client = TestClient(app)

    resp = client.get("/matches?cursor=bad-cursor")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid cursor"


def test_offsetless_first_page_still_works() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_EmptyMatchService())
    client = TestClient(app)

    resp = client.get("/matches")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_offset_query_param_is_deprecated_in_openapi() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_EmptyMatchService())
    client = TestClient(app)

    schema = client.get("/openapi.json").json()
    params = schema["paths"]["/matches"]["get"]["parameters"]
    offset_param = next(param for param in params if param["name"] == "offset")
    assert offset_param["deprecated"] is True


def test_deprecated_offset_query_param_still_works_temporarily() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_EmptyMatchService())
    client = TestClient(app)

    resp = client.get("/matches?offset=20")
    assert resp.status_code == 200
    assert resp.json()["offset"] == 20


def test_match_readiness_is_documented_in_openapi() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_EmptyMatchService())
    client = TestClient(app)

    schema = client.get("/openapi.json").json()
    match_item = schema["components"]["schemas"]["MatchItem"]["properties"]
    explanation = schema["components"]["schemas"]["MatchExplanation"]["properties"]

    assert "readiness" in match_item
    assert "readiness" in explanation


def test_classified_skills_are_optional_and_additive_in_openapi() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_EmptyMatchService())
    schema = TestClient(app).get("/openapi.json").json()

    for response_name in ("MatchSummary", "MatchExplanation", "MatchSkillAnalysis"):
        response_schema = schema["components"]["schemas"][response_name]
        assert "classified_skills" in response_schema["properties"]
        assert "classified_skills" not in response_schema.get("required", [])

    classified = schema["components"]["schemas"]["ClassifiedMatchSkills"]["properties"]
    assert set(classified) == {
        "required_matched_skills",
        "required_missing_skills",
        "preferred_matched_skills",
        "preferred_missing_skills",
        "unclassified_skills",
    }


def test_match_summary_returns_200_null_and_uses_current_user_scope() -> None:
    user = User(id=42, email="scoped@test.com", name="Scoped", is_active=True)
    service = _SummaryMatchService()
    app = _build_app_with_overrides(current_user=user, match_service=service)
    client = TestClient(app)

    response = client.get("/matches/501/summary")

    assert response.status_code == 200
    assert response.json() is None
    assert service.calls == [(42, 501)]


def test_match_evidence_is_documented_and_user_scoped() -> None:
    user = User(id=42, email="scoped@test.com", name="Scoped", is_active=True)
    service = _EvidenceMatchService()
    app = _build_app_with_overrides(current_user=user, match_service=service)
    client = TestClient(app)

    response = client.get("/matches/501/evidence")

    assert response.status_code == 200
    assert service.calls == [(42, 501)]
    schema = client.get("/openapi.json").json()
    assert "MatchEvidence" in schema["components"]["schemas"]


def test_analyze_match_is_posted_for_the_current_user_only() -> None:
    user = User(id=42, email="scoped@test.com", name="Scoped", is_active=True)
    service = _AnalyzeMatchService()
    app = _build_app_with_overrides(current_user=user, match_service=service)
    client = TestClient(app)

    response = client.post("/matches/501/analyze")

    assert response.status_code == 200
    assert response.json()["job_id"] == 501
    assert service.calls == [(42, 501)]


class _RankingTaskStore:
    def __init__(self, task: dict[str, object] | None = None) -> None:
        self.task = task
        self.created: list[tuple[str, int]] = []

    async def create(self, task_type: str, user_id: int) -> str:
        self.created.append((task_type, user_id))
        return "ranking-task"

    async def get(self, task_id: str) -> dict[str, object] | None:
        return self.task if task_id == "ranking-task" else None

    async def update(self, task_id: str, **fields: object) -> None:
        del task_id, fields


class _RankingRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int) -> bool:
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def ttl(self, key: str) -> int:
        del key
        return 600

    async def delete(self, key: str) -> int:
        self.values.pop(key, None)
        return 1

    async def eval(self, *args: object) -> int:
        del args
        return 1


def test_ranking_request_is_accepted_and_task_is_owner_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(id=42, email="scoped@test.com", name="Scoped", is_active=True)
    store = _RankingTaskStore({"user_id": 42, "status": "queued", "type": "match_ranking"})
    app = _build_app_with_overrides(current_user=user, match_service=_EmptyMatchService())
    app.dependency_overrides[get_task_store] = lambda: store
    redis = _RankingRedis()
    monkeypatch.setattr("app.infrastructure.redis_client.get_redis", lambda: redis)
    from app.workers import match_ranking

    monkeypatch.setattr(match_ranking.run_match_ranking_for_all_users, "delay", lambda *_: None)
    client = TestClient(app)

    accepted = client.post("/matches/rank")
    assert accepted.status_code == 202
    assert accepted.json() == {
        "task_id": "ranking-task",
        "state": "queued",
        "retry_after_seconds": None,
    }

    status_response = client.get("/matches/tasks/ranking-task")
    assert status_response.status_code == 200
    assert status_response.json()["user_id"] == 42

    store.task = {"user_id": 7, "status": "queued", "type": "match_ranking"}
    assert client.get("/matches/tasks/ranking-task").status_code == 404
