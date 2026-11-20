"""Tests for src/services/folders/folders.py."""

from unittest.mock import AsyncMock, patch

import pytest

from src.db.folders.folders import FolderCreate
from src.services.folders.folders import (
    add_folder_content,
    create_folder,
    delete_folder,
    get_folder,
    remove_folder_content,
)


def _bypass():
    """Patch RBAC + webhook side effects for the folders service."""
    return (
        patch(
            "src.services.folders.folders.check_resource_access",
            new_callable=AsyncMock,
        ),
        patch(
            "src.services.folders.folders.dispatch_webhooks",
            new_callable=AsyncMock,
        ),
    )


class TestFoldersService:
    @pytest.mark.asyncio
    async def test_create_root_and_nested_folder(self, db, org, admin_user, mock_request):
        rbac, webhooks = _bypass()
        with rbac, webhooks:
            root = await create_folder(
                mock_request,
                FolderCreate(name="Root", org_id=org.id),
                admin_user,
                db,
            )
            nested = await create_folder(
                mock_request,
                FolderCreate(
                    name="Child", org_id=org.id, parent_folder_uuid=root.folder_uuid
                ),
                admin_user,
                db,
            )

        assert root.name == "Root"
        assert root.folder_uuid.startswith("folder_")
        assert root.parent_folder_id is None
        assert nested.parent_folder_id == root.id

    @pytest.mark.asyncio
    async def test_get_folder_returns_subfolders_and_breadcrumbs(
        self, db, org, admin_user, mock_request
    ):
        rbac, webhooks = _bypass()
        with rbac, webhooks:
            root = await create_folder(
                mock_request,
                FolderCreate(name="Root", org_id=org.id),
                admin_user,
                db,
            )
            child = await create_folder(
                mock_request,
                FolderCreate(
                    name="Child", org_id=org.id, parent_folder_uuid=root.folder_uuid
                ),
                admin_user,
                db,
            )

            fetched_root = await get_folder(
                mock_request, root.folder_uuid, admin_user, db
            )
            fetched_child = await get_folder(
                mock_request, child.folder_uuid, admin_user, db
            )

        # Root shows its child as a subfolder and itself as the only breadcrumb.
        assert [s.folder_uuid for s in fetched_root.subfolders] == [child.folder_uuid]
        assert [b.folder_uuid for b in fetched_root.breadcrumbs] == [root.folder_uuid]

        # Child breadcrumbs walk up to the root: [root, child].
        assert [b.folder_uuid for b in fetched_child.breadcrumbs] == [
            root.folder_uuid,
            child.folder_uuid,
        ]

    @pytest.mark.asyncio
    async def test_add_and_remove_folder_content(
        self, db, org, course, admin_user, mock_request
    ):
        rbac, webhooks = _bypass()
        with rbac, webhooks:
            folder = await create_folder(
                mock_request,
                FolderCreate(name="Holder", org_id=org.id),
                admin_user,
                db,
            )

            with_item = await add_folder_content(
                mock_request,
                folder.folder_uuid,
                course.course_uuid,
                admin_user,
                db,
            )

            fetched = await get_folder(mock_request, folder.folder_uuid, admin_user, db)

            after_remove = await remove_folder_content(
                mock_request,
                folder.folder_uuid,
                course.course_uuid,
                admin_user,
                db,
            )

        item_uuids = [i.resource_uuid for i in with_item.items]
        assert course.course_uuid in item_uuids
        assert course.course_uuid in [i.resource_uuid for i in fetched.items]
        assert after_remove.items == []

    @pytest.mark.asyncio
    async def test_delete_folder(self, db, org, admin_user, mock_request):
        rbac, webhooks = _bypass()
        with rbac, webhooks:
            folder = await create_folder(
                mock_request,
                FolderCreate(name="Temp", org_id=org.id),
                admin_user,
                db,
            )
            result = await delete_folder(
                mock_request, folder.folder_uuid, admin_user, db
            )

            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await get_folder(mock_request, folder.folder_uuid, admin_user, db)

        assert result == {"detail": "Folder deleted"}
        assert exc_info.value.status_code == 404


class TestFolderHardening:
    @pytest.mark.asyncio
    @patch("src.services.folders.folders.dispatch_webhooks", new_callable=AsyncMock)
    async def test_create_folder_needs_update_on_parent(self, mock_webhooks, db, org):
        from datetime import datetime
        from fastapi import HTTPException
        from starlette.requests import Request
        from src.db.folders.folders import Folder, FolderCreate
        from src.db.resource_authors import (
            ResourceAuthor,
            ResourceAuthorshipEnum,
            ResourceAuthorshipStatusEnum,
        )
        from src.db.roles import Permission, Role, RoleTypeEnum
        from src.db.user_organizations import UserOrganization
        from src.db.users import PublicUser, User
        from src.tests.conftest import USER_RIGHTS
        from src.services.folders import folders as folders_service

        now = str(datetime.now())
        folder_rights = Permission(
            action_create=True, action_read=True, action_update=False, action_delete=False,
        ).model_dump()
        rights = USER_RIGHTS.model_dump()
        rights.update(folders=folder_rights)

        role = Role(
            id=303, name="maker", org_id=org.id, role_type=RoleTypeEnum.TYPE_ORGANIZATION,
            role_uuid="role_maker_f", rights=rights, creation_date=now, update_date=now,
        )
        db.add(role)
        for uid, uname in ((110, "alice"), (111, "bob")):
            db.add(User(
                id=uid, username=uname, first_name=uname, last_name="User",
                email=f"{uname}@test.com", password="x", user_uuid=f"user_{uname}",
                creation_date=now, update_date=now,
            ))
            db.add(UserOrganization(
                user_id=uid, org_id=org.id, role_id=role.id, creation_date=now, update_date=now,
            ))
        parent = Folder(
            id=150, name="alices", description="", public=False, org_id=org.id,
            folder_uuid="folder_alices", creation_date=now, update_date=now,
        )
        db.add(parent)
        db.add(ResourceAuthor(
            resource_uuid=parent.folder_uuid, user_id=110,
            authorship=ResourceAuthorshipEnum.CREATOR,
            authorship_status=ResourceAuthorshipStatusEnum.ACTIVE,
            creation_date=now, update_date=now,
        ))
        await db.commit()

        alice = PublicUser(id=110, username="alice", first_name="alice", last_name="User", email="alice@test.com", user_uuid="user_alice")
        bob = PublicUser(id=111, username="bob", first_name="bob", last_name="User", email="bob@test.com", user_uuid="user_bob")

        req = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "path_params": {}})
        body = FolderCreate(name="child", org_id=org.id, parent_folder_uuid=parent.folder_uuid)

        with pytest.raises(HTTPException) as exc:
            await folders_service.create_folder(req, body, bob, db)
        assert exc.value.status_code == 403

        child = await folders_service.create_folder(req, body, alice, db)
        assert child.parent_folder_id == parent.id
