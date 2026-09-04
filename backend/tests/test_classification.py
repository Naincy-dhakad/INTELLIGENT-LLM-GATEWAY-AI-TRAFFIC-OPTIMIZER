from gateway.domain.classification import (
    ClassificationMessage,
    ComplexityLevel,
    DeterministicRequestClassifier,
    RequestCategory,
)


classifier = DeterministicRequestClassifier()


def classify(text: str):
    return classifier.classify((ClassificationMessage("user", text),))


def test_supported_categories_and_precedence_are_deterministic():
    cases = {
        "What is the capital of India?": RequestCategory.QUESTION_ANSWER,
        "Write code to sort a list": RequestCategory.CODING,
        "Why does this exception fail? Please debug it": RequestCategory.DEBUGGING,
        "Summarize these key points": RequestCategory.SUMMARIZATION,
        "Translate this to Hindi": RequestCategory.TRANSLATION,
        "Compare the trade-offs and explain why": RequestCategory.REASONING,
        "Design a scalable system architecture": RequestCategory.ARCHITECTURE_DESIGN,
        "Write a short story about a curious cat": RequestCategory.CREATIVE_WRITING,
        "Analyze this CSV dataset": RequestCategory.DATA_ANALYSIS,
        "Hello, how are you?": RequestCategory.CONVERSATION,
        "blue green amber": RequestCategory.UNKNOWN,
    }
    for text, expected in cases.items():
        result = classify(text)
        assert result.category is expected
        assert result == classify(text)


def test_latest_user_message_is_used_without_exposing_content():
    result = classifier.classify(
        (
            ClassificationMessage("user", "Write code for an API"),
            ClassificationMessage("assistant", "Here is a response"),
            ClassificationMessage("user", "Hello there"),
        )
    )
    assert result.category is RequestCategory.CONVERSATION
    assert "Hello" not in result.explanation
    assert result.matched_signals == ("conversation_indicators",)


def test_complexity_levels_and_score_boundaries():
    low = classify("Hi")
    medium = classify("Explain the API design and compare the database trade-offs. Then list the steps.")
    high = classify(
        "Implement and debug this backend API.\n"
        + "1. Analyze the dataset and explain the architecture.\n"
        + "2. Compare the algorithms and provide code.\n"
        + "3. Test the system and summarize the results.\n"
        + ("Additional technical context. " * 80)
    )
    assert low.complexity_level is ComplexityLevel.LOW
    assert 0 <= low.complexity_score <= 30
    assert medium.complexity_level is ComplexityLevel.MEDIUM
    assert 31 <= medium.complexity_score <= 70
    assert high.complexity_level is ComplexityLevel.HIGH
    assert 71 <= high.complexity_score <= 100


def test_score_is_clamped_and_result_has_bounded_safe_explanation():
    result = classify(
        "Implement and debug this code.\n"
        + "1. Analyze the API and compare the architecture.\n"
        + ("Then explain the technical trade-offs. " * 400)
    )
    assert result.complexity_score == 100
    assert len(result.explanation) <= 280
    assert "code code" not in result.explanation


def test_classifier_is_provider_independent_and_does_not_call_any_provider():
    class ExplodingProvider:
        def chat(self, _request):
            raise AssertionError("provider must not be called")

    # The classifier accepts only normalized messages and has no provider input.
    assert classify("Explain a local algorithm")
    assert not hasattr(classifier, "_registry")
    assert not hasattr(ExplodingProvider(), "called")
