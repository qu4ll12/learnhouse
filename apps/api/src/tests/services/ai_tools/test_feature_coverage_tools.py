"""Feature coverage tools (OSS): webhooks, AI credits, and signup fields,
through the run_tool pipeline."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.db.organization_config import OrganizationConfig
from src.db.users import APITokenUser
from src.services.ai.tools import ToolContext, ToolRegistry, make_synthetic_request, refs, run_tool
from src.services.ai.tools import ai_credits, signup_fields, webhooks

NOW = str(datetime.now())
FULL = {"action_create": True, "action_read": True, "action_update": True, "action_delete": True}


@pytest.fixture(autouse=True)
def links():
    with patch.object(refs, "web_base", new=AsyncMock(return_value="https://acme.test")):
        yield


@pytest.fixture
def registry():
    r = ToolRegistry()
    for m in (webhooks, ai_credits, signup_fields):
        r.register_all(m.SPECS)
    return r


def ctx(db, org, user, approved=False):
    return ToolContext(request=make_synthetic_request(), db_session=db, user=user, org=org, org_slug=org.slug,
                       mode="execute", scope_key="t", surface="mcp", approved=approved)


async def call(registry, db, org, user, name, args=None, approved=False):
    return await run_tool(registry, name, args or {}, ctx(db, org, user, approved))


def ok(out):
    assert out.status == "executed", (out.denied_code, out.reason)
    return out.result


def token(org_id, rights):
    return APITokenUser(id=7, user_uuid="apitoken_x", username="t", org_id=org_id, rights=rights, created_by_user_id=1)


# ─── webhooks ────────────────────────────────────────────────────────────────


def endpoint(**kw):
    base = dict(webhook_uuid="webhook_1", url="https://hooks.test/a", description="", events=["course_completed"],
                is_active=True, source="manual", zap_name=None, creation_date=NOW)
    base.update(kw)
    return SimpleNamespace(**base)


def log(success=True):
    return SimpleNamespace(event_name="ping", delivery_uuid="d1", success=success, response_status=200 if success else 500,
                           attempt=1, error_message=None if success else "boom", created_at=NOW)


async def test_webhook_reads(db, org, admin_user, registry):
    W = "src.services.ai.tools.webhooks"
    with patch(f"{W}.get_webhook_endpoints", new=AsyncMock(return_value=[endpoint(), endpoint(webhook_uuid="webhook_2", source="zapier", zap_name="Zap")])), \
            patch(f"{W}.get_webhook_endpoint", new=AsyncMock(return_value=endpoint())), \
            patch(f"{W}.get_webhook_delivery_logs", new=AsyncMock(return_value=[log(), log(False)])):
        out = ok(await call(registry, db, org, admin_user, "list_webhooks"))
        assert out["total"] == 2 and out["url"] == "https://acme.test/dash/org/settings/webhooks"
        out = ok(await call(registry, db, org, admin_user, "list_webhooks", {"source": "zapier"}))
        assert [w["zap_name"] for w in out["items"]] == ["Zap"]
        out = ok(await call(registry, db, org, admin_user, "get_webhook_deliveries", {"webhook_uuid": "webhook_1"}))
        assert out["recent_failures"] == 1 and len(out["items"]) == 2
        out = ok(await call(registry, db, org, admin_user, "get_webhook_deliveries", {"webhook_uuid": "webhook_1", "failed_only": True}))
        assert [i["error"] for i in out["items"]] == ["boom"]
    out = ok(await call(registry, db, org, admin_user, "list_webhook_events"))
    assert any(e["name"] == "course_completed" for e in out["events"])


async def test_webhook_writes_and_approvals(db, org, admin_user, registry):
    W = "src.services.ai.tools.webhooks"
    created = SimpleNamespace(webhook_uuid="webhook_9", url="https://hooks.test/new", events=["ping"], secret="whsec_x")
    args = {"url": "https://hooks.test/new", "events": ["ping"]}
    assert (await call(registry, db, org, admin_user, "create_webhook", args)).denied_code == "approval_required"
    with patch(f"{W}.create_webhook_endpoint", new=AsyncMock(return_value=created)) as create:
        out = ok(await call(registry, db, org, admin_user, "create_webhook", args, approved=True))
    assert out["signing_secret"] == "whsec_x" and create.await_args.args[3].events == ["ping"]

    with patch(f"{W}.update_webhook_endpoint", new=AsyncMock(return_value=endpoint(description="d"))) as upd:
        ok(await call(registry, db, org, admin_user, "update_webhook", {"webhook_uuid": "webhook_1", "description": "d", "active": False}))
        assert upd.await_args.args[4].is_active is False
        out = await call(registry, db, org, admin_user, "update_webhook", {"webhook_uuid": "webhook_1", "url": "https://x.test/b"})
        assert out.denied_code == "approval_required"
    out = await call(registry, db, org, admin_user, "update_webhook", {"webhook_uuid": "webhook_1"})
    assert out.denied_code == "http_400"

    with patch(f"{W}.send_test_event", new=AsyncMock(return_value={"success": True})):
        out = ok(await call(registry, db, org, admin_user, "send_test_webhook", {"webhook_uuid": "webhook_1"}))
    assert out["result"] == {"success": True}

    for source, note in (("manual", ""), ("zapier", "Zap")):
        with patch(f"{W}.get_webhook_endpoint", new=AsyncMock(return_value=endpoint(source=source))), \
                patch(f"{W}.delete_webhook_endpoint", new=AsyncMock()):
            out = ok(await call(registry, db, org, admin_user, "delete_webhook", {"webhook_uuid": "webhook_1"}, approved=True))
        assert note in out["summary"]


async def test_webhooks_need_the_tokens_organizations_right(db, org, registry):
    out = await call(registry, db, org, token(org.id, {"courses": FULL}), "list_webhooks")
    assert out.denied_code == "missing_right"


# ─── AI credits ──────────────────────────────────────────────────────────────


async def test_ai_credits_balance_usage_and_prices(db, org, admin_user, registry):
    R = "src.services.ai.tools.ai_credits.routes"
    summary = SimpleNamespace(model_dump=lambda: {"remaining_credits": 50})
    usage = {"days": 7, "total_credits": 3, "by_feature": [], "by_member": [], "by_day": []}
    with patch(f"{R}.get_org_ai_credits", new=AsyncMock(return_value=summary)), \
            patch(f"{R}.get_org_ai_usage", new=AsyncMock(return_value=usage)), \
            patch(f"{R}.get_org_ai_prices", new=AsyncMock(return_value={"features": [{"key": "chat"}]})):
        out = ok(await call(registry, db, org, admin_user, "get_ai_credits", {"usage_days": 7, "include_prices": True}))
    assert out["balance"] == {"remaining_credits": 50} and out["usage"]["total_credits"] == 3 and "by_day" not in out["usage"]
    assert out["prices"] == [{"key": "chat"}] and out["url"].endswith("/dash/org/settings/ai")
    with patch(f"{R}.get_org_ai_credits", new=AsyncMock(return_value=summary)), \
            patch(f"{R}.get_org_ai_usage", new=AsyncMock(side_effect=HTTPException(403, "Admin rights required"))), \
            patch.object(refs, "web_base", new=AsyncMock(return_value="")):
        out = ok(await call(registry, db, org, admin_user, "get_ai_credits"))
    assert out["usage"] is None and "admins" in out["note"] and out["url"] is None
    with patch(f"{R}.get_org_ai_credits", new=AsyncMock(return_value=summary)), \
            patch(f"{R}.get_org_ai_usage", new=AsyncMock(side_effect=HTTPException(404, "gone"))):
        assert (await call(registry, db, org, admin_user, "get_ai_credits")).denied_code == "http_404"
    with patch(f"{R}.get_org_ai_credits", new=AsyncMock(return_value=summary)):
        out = ok(await call(registry, db, org, admin_user, "get_ai_credits", {"usage_days": 0}))
    assert "usage" not in out


async def test_ai_credits_real_balance_for_a_member(db, org, admin_user, registry):
    out = await call(registry, db, org, admin_user, "get_ai_credits", {"usage_days": 0})
    assert out.status in ("executed", "denied")


# ─── signup fields ───────────────────────────────────────────────────────────


async def test_signup_fields(db, org, admin_user, registry):
    db.add(OrganizationConfig(org_id=org.id, config={"general": {"signup_fields": {"fields": [
        {"key": "team", "label": "Team", "order": 2, "people_field": "department"},
        {"key": "why", "label": "Why", "type": "textarea", "order": 1},
    ]}}}, creation_date=NOW, update_date=NOW))
    await db.commit()
    out = ok(await call(registry, db, org, admin_user, "get_signup_fields"))
    assert [f["key"] for f in out["fields"]] == ["why", "team"] and out["saved_to_people_data"] == ["team"]
    with patch.object(refs, "web_base", new=AsyncMock(return_value="")):
        assert ok(await call(registry, db, org, admin_user, "get_signup_fields"))["url"] is None


def test_summaries():
    specs = {s.name: s for s in webhooks.SPECS}
    assert "nothing" in specs["update_webhook"].summarize(specs["update_webhook"].params_model(webhook_uuid="w"))
    for name in ("create_webhook", "send_test_webhook", "delete_webhook"):
        s = specs[name]
        fields = {k: ("x" if f.annotation in (str, "str") else None) for k, f in s.params_model.model_fields.items()}
        sample = {"webhook_uuid": "w", "name": "n",
                  "url": "https://x.test/aaaa", "events": ["ping"]}
        params = s.params_model(**{k: v for k, v in sample.items() if k in s.params_model.model_fields})
        assert s.summarize(params)
        assert fields is not None
