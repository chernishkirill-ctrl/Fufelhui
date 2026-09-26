"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-26 17:52:18.091995
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=True),
    sa.Column('first_name', sa.String(length=128), nullable=True),
    sa.Column('role', sa.Enum('owner', 'admin', 'realtor', name='user_role', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('active', 'blocked', 'removed', name='user_status', native_enum=False, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_telegram_id'), ['telegram_id'], unique=True)

    op.create_table('daily_reports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('realtor_id', sa.Integer(), nullable=False),
    sa.Column('report_date', sa.Date(), nullable=False),
    sa.Column('new_clients', sa.Integer(), nullable=False),
    sa.Column('calls', sa.Integer(), nullable=False),
    sa.Column('leads_processed', sa.Integer(), nullable=False),
    sa.Column('viewings', sa.Integer(), nullable=False),
    sa.Column('new_properties', sa.Integer(), nullable=False),
    sa.Column('deals_closed', sa.Integer(), nullable=False),
    sa.Column('report_text', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['realtor_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('realtor_id', 'report_date', name='uq_daily_report_realtor_date')
    )
    with op.batch_alter_table('daily_reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_daily_reports_realtor_id'), ['realtor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_daily_reports_report_date'), ['report_date'], unique=False)

    op.create_table('invites',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('used_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['used_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('invites', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_invites_token'), ['token'], unique=True)

    op.create_table('properties',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('source_name', sa.String(length=64), nullable=True),
    sa.Column('offer_type', sa.Enum('sale', 'rent', name='offer_type', native_enum=False, length=32), nullable=False),
    sa.Column('property_type', sa.String(length=64), nullable=True),
    sa.Column('title', sa.String(length=256), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('price', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=8), nullable=False),
    sa.Column('city', sa.String(length=128), nullable=True),
    sa.Column('address', sa.String(length=256), nullable=True),
    sa.Column('district', sa.String(length=128), nullable=True),
    sa.Column('latitude', sa.Double(), nullable=True),
    sa.Column('longitude', sa.Double(), nullable=True),
    sa.Column('show_exact_location', sa.Boolean(), nullable=False),
    sa.Column('rooms', sa.Integer(), nullable=True),
    sa.Column('area', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('floor', sa.Integer(), nullable=True),
    sa.Column('floors_total', sa.Integer(), nullable=True),
    sa.Column('features', sa.JSON(), nullable=True),
    sa.Column('photos', sa.JSON(), nullable=True),
    sa.Column('owner_name', sa.String(length=128), nullable=True),
    sa.Column('owner_phone', sa.String(length=64), nullable=True),
    sa.Column('internal_comment', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('draft', 'active', 'published', 'reserved', 'sold', 'rented', 'inactive', 'archived', name='property_status', native_enum=False, length=32), nullable=False),
    sa.Column('responsible_realtor_id', sa.Integer(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('telegraph_url', sa.Text(), nullable=True),
    sa.Column('public_message_id', sa.BigInteger(), nullable=True),
    sa.Column('public_extra_ids', sa.JSON(), nullable=True),
    sa.Column('base_message_id', sa.BigInteger(), nullable=True),
    sa.Column('archive_message_id', sa.BigInteger(), nullable=True),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('archived_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['responsible_realtor_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('properties', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_properties_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_properties_responsible_realtor_id'), ['responsible_realtor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_properties_status'), ['status'], unique=False)

    op.create_table('leads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('property_id', sa.Integer(), nullable=True),
    sa.Column('client_telegram_id', sa.BigInteger(), nullable=True),
    sa.Column('client_username', sa.String(length=64), nullable=True),
    sa.Column('client_name', sa.String(length=128), nullable=True),
    sa.Column('phone', sa.String(length=64), nullable=True),
    sa.Column('preferred_time', sa.String(length=128), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('internal_comment', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('new', 'assigned', 'in_progress', 'viewing_scheduled', 'viewing_done', 'closed', 'cancelled', name='lead_status', native_enum=False, length=32), nullable=False),
    sa.Column('assigned_realtor_id', sa.Integer(), nullable=True),
    sa.Column('work_message_id', sa.BigInteger(), nullable=True),
    sa.Column('viewing_at', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('assigned_at', sa.DateTime(), nullable=True),
    sa.Column('viewing_done_at', sa.DateTime(), nullable=True),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assigned_realtor_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_leads_assigned_realtor_id'), ['assigned_realtor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_leads_client_telegram_id'), ['client_telegram_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_leads_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_leads_property_id'), ['property_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_leads_status'), ['status'], unique=False)

    op.create_table('property_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('property_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('field', sa.String(length=64), nullable=True),
    sa.Column('old_value', sa.Text(), nullable=True),
    sa.Column('new_value', sa.Text(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('property_history', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_property_history_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_property_history_property_id'), ['property_id'], unique=False)

    op.create_table('deals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('property_id', sa.Integer(), nullable=True),
    sa.Column('realtor_id', sa.Integer(), nullable=True),
    sa.Column('client_id', sa.Integer(), nullable=True),
    sa.Column('deal_type', sa.Enum('sale', 'rent', 'other', name='deal_type', native_enum=False, length=32), nullable=False),
    sa.Column('deal_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('commission', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=False),
    sa.Column('deal_date', sa.Date(), nullable=False),
    sa.Column('status', sa.Enum('confirmed', 'cancelled', name='deal_status', native_enum=False, length=32), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('deals_message_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['leads.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['realtor_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('deals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_deals_deal_date'), ['deal_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_deals_property_id'), ['property_id'], unique=False)
        batch_op.create_index('ix_deals_realtor_date', ['realtor_id', 'deal_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_deals_realtor_id'), ['realtor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_deals_status'), ['status'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('deals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_deals_status'))
        batch_op.drop_index(batch_op.f('ix_deals_realtor_id'))
        batch_op.drop_index('ix_deals_realtor_date')
        batch_op.drop_index(batch_op.f('ix_deals_property_id'))
        batch_op.drop_index(batch_op.f('ix_deals_deal_date'))

    op.drop_table('deals')
    with op.batch_alter_table('property_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_property_history_property_id'))
        batch_op.drop_index(batch_op.f('ix_property_history_created_at'))

    op.drop_table('property_history')
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_leads_status'))
        batch_op.drop_index(batch_op.f('ix_leads_property_id'))
        batch_op.drop_index(batch_op.f('ix_leads_created_at'))
        batch_op.drop_index(batch_op.f('ix_leads_client_telegram_id'))
        batch_op.drop_index(batch_op.f('ix_leads_assigned_realtor_id'))

    op.drop_table('leads')
    with op.batch_alter_table('properties', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_properties_status'))
        batch_op.drop_index(batch_op.f('ix_properties_responsible_realtor_id'))
        batch_op.drop_index(batch_op.f('ix_properties_created_at'))

    op.drop_table('properties')
    with op.batch_alter_table('invites', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invites_token'))

    op.drop_table('invites')
    with op.batch_alter_table('daily_reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_daily_reports_report_date'))
        batch_op.drop_index(batch_op.f('ix_daily_reports_realtor_id'))

    op.drop_table('daily_reports')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_telegram_id'))

    op.drop_table('users')
    op.drop_table('app_settings')
