"""commit set metrics index

Adds the composite index used to resolve commit sets by repository and
committer-date range (the ``from`` / ``to`` metrics filters).

Revision ID: c4f8a1b2e9d3
Revises: 3803a27ee240
Create Date: 2026-10-06 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4f8a1b2e9d3'
down_revision: Union[str, None] = '3803a27ee240'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('commits', schema=None) as batch_op:
        batch_op.create_index(
            'ix_commits_repo_committed_at', ['repository_id', 'committed_at'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('commits', schema=None) as batch_op:
        batch_op.drop_index('ix_commits_repo_committed_at')
