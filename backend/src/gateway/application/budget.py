"""Application budget lookup, independent of database implementation."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


class HistoricalSpendRepository(Protocol):
    def get_accumulated_spend(self, principal_id: str) -> Decimal:
        ...


class BudgetUnavailable(Exception):
    """Historical spend cannot be obtained safely."""


@dataclass(frozen=True)
class BudgetSnapshot:
    configured_budget_usd: Decimal
    historical_spend_usd: Decimal

    @property
    def remaining_budget_usd(self) -> Decimal:
        return self.configured_budget_usd - self.historical_spend_usd


class BudgetService:
    def __init__(self, repository: HistoricalSpendRepository | None, enabled: bool) -> None:
        self._repository = repository
        self._enabled = enabled

    def snapshot(self, principal_id: str | None, configured_budget_usd: Decimal) -> BudgetSnapshot:
        if not self._enabled or self._repository is None or principal_id is None:
            raise BudgetUnavailable
        try:
            spend = self._repository.get_accumulated_spend(principal_id)
        except Exception as exc:
            raise BudgetUnavailable from exc
        return BudgetSnapshot(configured_budget_usd, spend)
