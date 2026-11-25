"""add MATH_STEPS to assignmenttasktypeenum

Adds the MATH_STEPS assignment task type: the learner solves a linear equation
step by step and every transition is checked by the MATHCDT checker
(services/courses/activities/math_steps).

Revision ID: m4t5h6s7t8e9
Revises: c0m1u2n3i4t5
Create Date: 2026-11-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa  # noqa: F401
import sqlmodel  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = 'm4t5h6s7t8e9'
down_revision: Union[str, None] = 'c0m1u2n3i4t5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block; commit the
    # migration's implicit transaction first (mirrors the CUSTOM-type migration).
    op.execute("COMMIT")
    op.execute("ALTER TYPE assignmenttasktypeenum ADD VALUE IF NOT EXISTS 'MATH_STEPS'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values directly.
    pass
