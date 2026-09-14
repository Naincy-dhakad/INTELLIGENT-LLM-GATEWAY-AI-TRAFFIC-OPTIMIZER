from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from gateway.application.usage_tracking import UsageRecord
from gateway.infrastructure.database.models import GatewayUsageRecordModel
from gateway.infrastructure.database.session import DatabaseResource


class SqlAlchemyUsageRecordRepository:
    def __init__(self, resource: DatabaseResource) -> None:
        self._resource = resource
        self._session_factory = resource.session_factory

    def record_usage(self, record: UsageRecord) -> None:
        model = GatewayUsageRecordModel(
            request_id=record.request_id,
            principal_id=record.principal_id,
            provider_id=record.provider_id,
            model_id=record.model_id,
            outcome=record.outcome,
            error_code=record.error_code,
            error_category=record.error_category,
            http_status=record.http_status,
            attempt_count=record.attempt_count,
            fallback_used=record.fallback_used,
            routing_objective=record.routing_objective,
            routing_policy_version=record.routing_policy_version,
            classification_category=record.classification_category,
            classification_complexity_level=record.classification_complexity_level,
            classification_complexity_score=record.classification_complexity_score,
            estimated_cost_usd=record.estimated_cost_usd,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            total_tokens=record.total_tokens,
            estimated_latency_ms=record.estimated_latency_ms,
            actual_gateway_latency_ms=record.actual_gateway_latency_ms,
            created_at=record.created_at,
        )
        with self._session_factory.begin() as session:
            session.add(model)

    def get_accumulated_spend(self, principal_id: str) -> Decimal:
        statement = select(
            func.coalesce(func.sum(GatewayUsageRecordModel.estimated_cost_usd), 0)
        ).where(GatewayUsageRecordModel.principal_id == principal_id)
        with self._session_factory() as session:
            value = session.execute(statement).scalar_one()
        return Decimal(value)
