"""The paywall hooks: OSS asks EE whether a sold resource's content is locked."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.core.ee_hooks import is_ee_resource_locked, require_ee_resource_paid_access
from src.db.courses.translations import CourseTranslation
from src.db.users import AnonymousUser, APITokenUser, PublicUser

USER = PublicUser(id=5, username="u", first_name="", last_name="", email="u@x.io", user_uuid="user_5")


def _hooks(side_effect=None):
    return SimpleNamespace(require_resource_paid_access=AsyncMock(side_effect=side_effect))


@pytest.mark.asyncio
async def test_without_ee_nothing_is_locked():
    with patch("src.core.ee_hooks.get_ee_hooks", return_value=None):
        await require_ee_resource_paid_access(None, None, USER, "community_1")
        assert await is_ee_resource_locked(None, None, AnonymousUser(), "community_1") is False


@pytest.mark.asyncio
async def test_hook_gets_the_request_and_user():
    hooks = _hooks()
    with patch("src.core.ee_hooks.get_ee_hooks", return_value=hooks):
        await require_ee_resource_paid_access("req", "db", USER, "course_1")
    hooks.require_resource_paid_access.assert_awaited_once_with("req", "course_1", USER, "db")


@pytest.mark.asyncio
async def test_api_tokens_are_never_paywalled():
    hooks = _hooks(HTTPException(status_code=402))
    with patch("src.core.ee_hooks.get_ee_hooks", return_value=hooks):
        await require_ee_resource_paid_access(None, None, APITokenUser(org_id=1), "board_1")
    hooks.require_resource_paid_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_locked_is_a_402_and_other_errors_propagate():
    with patch("src.core.ee_hooks.get_ee_hooks", return_value=_hooks(HTTPException(status_code=402))):
        assert await is_ee_resource_locked(None, None, USER, "course_1") is True
        with pytest.raises(HTTPException) as e:
            await require_ee_resource_paid_access(None, None, USER, "course_1")
        assert e.value.status_code == 402
    with patch("src.core.ee_hooks.get_ee_hooks", return_value=_hooks(HTTPException(status_code=404))):
        with pytest.raises(HTTPException):
            await is_ee_resource_locked(None, None, USER, "course_1")
    with patch("src.core.ee_hooks.get_ee_hooks", return_value=_hooks()):
        assert await is_ee_resource_locked(None, None, USER, "course_1") is False
