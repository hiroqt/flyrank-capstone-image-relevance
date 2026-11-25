"""initial schema: images, tags, embeddings, posts, suggestions, reviews, jobs, cost log

Generated with alembic --autogenerate from app/db/models.py, then reviewed.
Indexes lead with tenant_id (tenant isolation); see PLAN.md section 3.

Revision ID: 0001
Revises: 
Create Date: 2026-11-25 04:17:09.420718
"""
from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector powers the embeddings.vector column
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('tenants',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("id ~ '^[a-z0-9][a-z0-9-]{1,39}$'", name=op.f('ck_tenants_id_format')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tenants'))
    )
    op.create_table('embeddings',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.Column('owner_type', sa.String(length=10), nullable=False),
    sa.Column('owner_id', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('dim', sa.Integer(), nullable=False),
    sa.Column('text_hash', sa.String(length=64), nullable=False),
    sa.Column('vector', pgvector.sqlalchemy.vector.VECTOR(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("owner_type IN ('image', 'post', 'label')", name=op.f('ck_embeddings_owner_type')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_embeddings_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_embeddings')),
    sa.UniqueConstraint('tenant_id', 'owner_type', 'owner_id', 'model', name=op.f('uq_embeddings_tenant_id_owner_type_owner_id_model'))
    )
    op.create_index(op.f('ix_embeddings_tenant_id_owner_type_model'), 'embeddings', ['tenant_id', 'owner_type', 'model'], unique=False)
    op.create_table('idempotency_keys',
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.Column('key', sa.String(length=200), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('response', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_idempotency_keys_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('tenant_id', 'key', name=op.f('pk_idempotency_keys'))
    )
    op.create_table('images',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('file_path', sa.Text(), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('license', sa.Text(), nullable=True),
    sa.Column('photographer', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('subject', sa.Text(), nullable=True),
    sa.Column('subject_key', sa.String(length=40), nullable=True),
    sa.Column('subject_key_score', sa.Float(), nullable=True),
    sa.Column('category', sa.String(length=20), nullable=True),
    sa.Column('caption', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('flag_reasons', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('vision_model', sa.Text(), nullable=True),
    sa.Column('prompt_version', sa.String(length=20), nullable=True),
    sa.Column('raw_response', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'processing', 'tagged', 'flagged', 'failed')", name=op.f('ck_images_status')),
    sa.CheckConstraint('confidence IS NULL OR (confidence >= 0 AND confidence <= 1)', name=op.f('ck_images_confidence_range')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_images_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_images')),
    sa.UniqueConstraint('tenant_id', 'sha256', name=op.f('uq_images_tenant_id_sha256'))
    )
    op.create_index(op.f('ix_images_tenant_id_status'), 'images', ['tenant_id', 'status'], unique=False)
    op.create_index(op.f('ix_images_tenant_id_subject_key'), 'images', ['tenant_id', 'subject_key'], unique=False)
    op.create_table('jobs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.Column('type', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='queued', nullable=False),
    sa.Column('force', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('total', sa.Integer(), server_default='0', nullable=False),
    sa.Column('processed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('succeeded', sa.Integer(), server_default='0', nullable=False),
    sa.Column('flagged', sa.Integer(), server_default='0', nullable=False),
    sa.Column('failed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'partial', 'failed')", name=op.f('ck_jobs_status')),
    sa.CheckConstraint("type IN ('process_images', 'process_posts')", name=op.f('ck_jobs_type')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_jobs_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    op.create_index(op.f('ix_jobs_tenant_id_status'), 'jobs', ['tenant_id', 'status'], unique=False)
    op.create_table('posts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('subject_key', sa.String(length=40), nullable=True),
    sa.Column('subject_key_score', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'embedded', 'failed')", name=op.f('ck_posts_status')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_posts_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_posts')),
    sa.UniqueConstraint('tenant_id', 'slug', name=op.f('uq_posts_tenant_id_slug'))
    )
    op.create_table('alerts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('level', sa.String(length=10), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("level IN ('warning', 'error')", name=op.f('ck_alerts_level')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_alerts_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_alerts_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alerts'))
    )
    op.create_index(op.f('ix_alerts_tenant_id_created_at'), 'alerts', ['tenant_id', 'created_at'], unique=False)
    op.create_table('image_tags',
    sa.Column('image_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('tag', sa.String(length=80), nullable=False),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.CheckConstraint("kind IN ('subject', 'category', 'attribute')", name=op.f('ck_image_tags_kind')),
    sa.ForeignKeyConstraint(['image_id'], ['images.id'], name=op.f('fk_image_tags_image_id_images'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_image_tags_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('image_id', 'kind', 'tag', name=op.f('pk_image_tags'))
    )
    op.create_index(op.f('ix_image_tags_tenant_id_tag'), 'image_tags', ['tenant_id', 'tag'], unique=False)
    op.create_table('job_items',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.Column('item_type', sa.String(length=10), nullable=False),
    sa.Column('item_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='queued', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('max_attempts', sa.Integer(), server_default='3', nullable=False),
    sa.Column('next_run_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("item_type IN ('image', 'post')", name=op.f('ck_job_items_item_type')),
    sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'flagged', 'failed')", name=op.f('ck_job_items_status')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_job_items_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_job_items_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_items')),
    sa.UniqueConstraint('job_id', 'item_id', name=op.f('uq_job_items_job_id_item_id'))
    )
    op.create_index(op.f('ix_job_items_status_next_run_at'), 'job_items', ['status', 'next_run_at'], unique=False)
    op.create_table('suggestions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.Column('post_id', sa.UUID(), nullable=False),
    sa.Column('image_id', sa.UUID(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('similarity', sa.Float(), nullable=False),
    sa.Column('verdict', sa.String(length=10), nullable=False),
    sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('thresholds', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('guard_version', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("verdict IN ('accepted', 'rejected')", name=op.f('ck_suggestions_verdict')),
    sa.ForeignKeyConstraint(['image_id'], ['images.id'], name=op.f('fk_suggestions_image_id_images'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['post_id'], ['posts.id'], name=op.f('fk_suggestions_post_id_posts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_suggestions_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_suggestions')),
    sa.UniqueConstraint('tenant_id', 'post_id', 'image_id', 'guard_version', name=op.f('uq_suggestions_tenant_id_post_id_image_id_guard_version'))
    )
    op.create_index(op.f('ix_suggestions_tenant_id_post_id_rank'), 'suggestions', ['tenant_id', 'post_id', 'rank'], unique=False)
    op.create_table('ai_calls',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('job_item_id', sa.BigInteger(), nullable=True),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('operation', sa.String(length=10), nullable=False),
    sa.Column('entity_type', sa.String(length=10), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=False),
    sa.Column('attempt', sa.Integer(), server_default='1', nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('tokens_estimated', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('cost_usd', sa.Numeric(precision=12, scale=8), server_default='0', nullable=False),
    sa.Column('tier', sa.String(length=10), nullable=False),
    sa.Column('latency_ms', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("operation IN ('vision', 'embed')", name=op.f('ck_ai_calls_operation')),
    sa.CheckConstraint("status IN ('ok', 'invalid_output', 'error', 'rate_limited', 'budget_blocked')", name=op.f('ck_ai_calls_status')),
    sa.CheckConstraint("tier IN ('free', 'paid', 'local')", name=op.f('ck_ai_calls_tier')),
    sa.CheckConstraint('cost_usd >= 0', name=op.f('ck_ai_calls_cost_non_negative')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_ai_calls_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['job_item_id'], ['job_items.id'], name=op.f('fk_ai_calls_job_item_id_job_items'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_ai_calls_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_calls'))
    )
    op.create_index(op.f('ix_ai_calls_entity_type_entity_id'), 'ai_calls', ['entity_type', 'entity_id'], unique=False)
    op.create_index(op.f('ix_ai_calls_job_id'), 'ai_calls', ['job_id'], unique=False)
    op.create_index(op.f('ix_ai_calls_tenant_id_created_at'), 'ai_calls', ['tenant_id', 'created_at'], unique=False)
    op.create_table('reviews',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('tenant_id', sa.String(length=40), nullable=False),
    sa.Column('suggestion_id', sa.UUID(), nullable=False),
    sa.Column('decision', sa.String(length=10), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('reviewer', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("decision IN ('approved', 'rejected')", name=op.f('ck_reviews_decision')),
    sa.ForeignKeyConstraint(['suggestion_id'], ['suggestions.id'], name=op.f('fk_reviews_suggestion_id_suggestions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_reviews_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reviews')),
    sa.UniqueConstraint('suggestion_id', name=op.f('uq_reviews_suggestion_id'))
    )
    op.create_index(op.f('ix_reviews_tenant_id_decision'), 'reviews', ['tenant_id', 'decision'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_reviews_tenant_id_decision'), table_name='reviews')
    op.drop_table('reviews')
    op.drop_index(op.f('ix_ai_calls_tenant_id_created_at'), table_name='ai_calls')
    op.drop_index(op.f('ix_ai_calls_job_id'), table_name='ai_calls')
    op.drop_index(op.f('ix_ai_calls_entity_type_entity_id'), table_name='ai_calls')
    op.drop_table('ai_calls')
    op.drop_index(op.f('ix_suggestions_tenant_id_post_id_rank'), table_name='suggestions')
    op.drop_table('suggestions')
    op.drop_index(op.f('ix_job_items_status_next_run_at'), table_name='job_items')
    op.drop_table('job_items')
    op.drop_index(op.f('ix_image_tags_tenant_id_tag'), table_name='image_tags')
    op.drop_table('image_tags')
    op.drop_index(op.f('ix_alerts_tenant_id_created_at'), table_name='alerts')
    op.drop_table('alerts')
    op.drop_table('posts')
    op.drop_index(op.f('ix_jobs_tenant_id_status'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_images_tenant_id_subject_key'), table_name='images')
    op.drop_index(op.f('ix_images_tenant_id_status'), table_name='images')
    op.drop_table('images')
    op.drop_table('idempotency_keys')
    op.drop_index(op.f('ix_embeddings_tenant_id_owner_type_model'), table_name='embeddings')
    op.drop_table('embeddings')
    op.drop_table('tenants')
    op.execute("DROP EXTENSION IF EXISTS vector")
