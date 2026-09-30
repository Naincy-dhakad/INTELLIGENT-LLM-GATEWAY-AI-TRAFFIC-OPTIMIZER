from dataclasses import dataclass
from enum import StrEnum

from gateway.domain.execution import AttemptOutcome, AttemptRole, ExplainableRoutingResult
from gateway.domain.routing import _check_explanation_identifier


class SafeExecutionOutcome(StrEnum):
    NOT_STARTED = "not_started"
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True)
class SafeRoutingExplanation:
    """Allowlisted routing and execution summary with no internal trace data."""

    selected_provider_id: str
    selected_model_id: str
    objective: str
    selection_reason: str
    policy_version: str
    attempt_count: int
    fallback_used: bool
    execution_outcome: SafeExecutionOutcome

    def __post_init__(self) -> None:
        _check_explanation_identifier(self.selected_provider_id, "selected_provider_id")
        _check_explanation_identifier(self.selected_model_id, "selected_model_id")
        _check_explanation_identifier(self.objective, "objective")
        _check_explanation_identifier(self.policy_version, "policy_version")
        if not isinstance(self.selection_reason, str) or not self.selection_reason.strip():
            raise ValueError("selection_reason must be non-empty text")
        if len(self.selection_reason) > 512 or "\n" in self.selection_reason or "\r" in self.selection_reason:
            raise ValueError("selection_reason must be a bounded single-line value")
        if not isinstance(self.attempt_count, int) or not 0 <= self.attempt_count <= 3:
            raise ValueError("attempt_count must be between zero and three")
        if not isinstance(self.fallback_used, bool):
            raise TypeError("fallback_used must be a boolean")
        if not isinstance(self.execution_outcome, SafeExecutionOutcome):
            raise TypeError("execution_outcome must use SafeExecutionOutcome")

    @classmethod
    def from_internal(cls, internal: ExplainableRoutingResult) -> "SafeRoutingExplanation":
        if not isinstance(internal, ExplainableRoutingResult):
            raise TypeError("internal must be an ExplainableRoutingResult")
        routing = internal.routing_explanation
        if routing.selection is None:
            raise ValueError("routing explanation must include a selected candidate")

        execution = internal.execution_explanation
        if execution is None or not execution.attempts:
            attempt_count = 0
            fallback_used = False
            outcome = SafeExecutionOutcome.NOT_STARTED
        else:
            attempt_count = len(execution.attempts)
            fallback_used = any(
                attempt.attempt_role is AttemptRole.FALLBACK
                for attempt in execution.attempts
            )
            final_outcome = execution.attempts[-1].outcome
            outcome = (
                SafeExecutionOutcome.SUCCESS
                if final_outcome is AttemptOutcome.SUCCESS
                else SafeExecutionOutcome.FAILURE
            )

        return cls(
            selected_provider_id=routing.selection.selected.provider_id,
            selected_model_id=routing.selection.selected.model_id,
            objective=routing.objective,
            selection_reason=routing.selection.reason,
            policy_version=routing.policy_version,
            attempt_count=attempt_count,
            fallback_used=fallback_used,
            execution_outcome=outcome,
        )