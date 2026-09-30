from dataclasses import dataclass
from enum import StrEnum

from gateway.domain.provider import ProviderErrorCategory
from gateway.domain.routing import RoutingExplanation, _check_explanation_identifier, _check_tuple


class AttemptRole(StrEnum):
    INITIAL = "initial"
    RETRY = "retry"
    FALLBACK = "fallback"


class AttemptOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True)
class AttemptExplanation:
    provider_id: str
    model_id: str
    attempt_number: int
    attempt_role: AttemptRole
    outcome: AttemptOutcome
    error_category: ProviderErrorCategory | None = None

    def __post_init__(self) -> None:
        _check_explanation_identifier(self.provider_id, "provider_id")
        _check_explanation_identifier(self.model_id, "model_id")
        if not isinstance(self.attempt_number, int) or self.attempt_number < 1:
            raise ValueError("attempt_number must be a positive integer")
        if not isinstance(self.attempt_role, AttemptRole):
            raise TypeError("attempt_role must use AttemptRole")
        if not isinstance(self.outcome, AttemptOutcome):
            raise TypeError("outcome must use AttemptOutcome")
        if self.outcome is AttemptOutcome.FAILURE and not isinstance(self.error_category, ProviderErrorCategory):
            raise ValueError("failed attempts require a normalized provider error category")
        if self.outcome is AttemptOutcome.SUCCESS and self.error_category is not None:
            raise ValueError("successful attempts must not include an error category")


@dataclass(frozen=True)
class ExecutionExplanation:
    initial_provider_id: str
    initial_model_id: str
    attempts: tuple[AttemptExplanation, ...]

    def __post_init__(self) -> None:
        _check_explanation_identifier(self.initial_provider_id, "initial_provider_id")
        _check_explanation_identifier(self.initial_model_id, "initial_model_id")
        _check_tuple(self.attempts, "attempts")
        for index, attempt in enumerate(self.attempts, start=1):
            if not isinstance(attempt, AttemptExplanation):
                raise TypeError("attempts must contain AttemptExplanation values")
            if attempt.attempt_number != index:
                raise ValueError("attempt numbers must be sequential and start at one")
        if self.attempts:
            first = self.attempts[0]
            if first.attempt_role is not AttemptRole.INITIAL:
                raise ValueError("the first attempt must have the initial role")
            if first.provider_id != self.initial_provider_id or first.model_id != self.initial_model_id:
                raise ValueError("the initial attempt must match the initial provider and model")
        if any(attempt.attempt_role is AttemptRole.INITIAL for attempt in self.attempts[1:]):
            raise ValueError("only the first attempt may have the initial role")


@dataclass(frozen=True)
class ExplainableRoutingResult:
    routing_explanation: RoutingExplanation
    execution_explanation: ExecutionExplanation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.routing_explanation, RoutingExplanation):
            raise TypeError("routing_explanation must be a RoutingExplanation")
        if self.execution_explanation is not None:
            if not isinstance(self.execution_explanation, ExecutionExplanation):
                raise TypeError("execution_explanation must be an ExecutionExplanation or None")
            selection = self.routing_explanation.selection
            if selection is None:
                raise ValueError("routing explanation must include a selected candidate")
            if self.execution_explanation.initial_provider_id != selection.selected.provider_id:
                raise ValueError("execution initial provider must match the routing selection")
            if self.execution_explanation.initial_model_id != selection.selected.model_id:
                raise ValueError("execution initial model must match the routing selection")