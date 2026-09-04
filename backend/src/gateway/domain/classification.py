"""Deterministic, local request classification and complexity scoring."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence


class RequestCategory(StrEnum):
    QUESTION_ANSWER = "question_answer"
    CODING = "coding"
    DEBUGGING = "debugging"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    REASONING = "reasoning"
    ARCHITECTURE_DESIGN = "architecture_design"
    CREATIVE_WRITING = "creative_writing"
    DATA_ANALYSIS = "data_analysis"
    CONVERSATION = "conversation"
    UNKNOWN = "unknown"


class ComplexityLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class ClassificationMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ClassificationResult:
    category: RequestCategory
    complexity_level: ComplexityLevel
    complexity_score: int
    matched_signals: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class _CategoryRule:
    category: RequestCategory
    patterns: tuple[re.Pattern[str], ...]
    signal: str


_CATEGORY_RULES = (
    _CategoryRule(
        RequestCategory.DEBUGGING,
        tuple(
            re.compile(pattern)
            for pattern in (
                r"\bdebug(?:ging)?\b",
                r"\b(?:bug|exception|stack\s+trace|traceback)\b",
                r"\bnot\s+working\b",
                r"\bfix\s+(?:this|the|my)\b",
                r"\b(?:throws?|fails?|failure)\b",
            )
        ),
        "debugging_indicators",
    ),
    _CategoryRule(
        RequestCategory.CODING,
        tuple(
            re.compile(pattern)
            for pattern in (
                r"\bwrite\s+(?:some\s+)?code\b",
                r"\bimplement\b",
                r"\b(?:function|algorithm|program)\b",
                r"\b(?:python|javascript|typescript|java|c\+\+|rust|golang)\s+code\b",
                r"```[\s\S]*```",
                r"\b(?:code|class)\s+(?:for|that|to)\b",
                r"\bcode\b",
            )
        ),
        "coding_indicators",
    ),
    _CategoryRule(
        RequestCategory.ARCHITECTURE_DESIGN,
        tuple(
            re.compile(pattern)
            for pattern in (
                r"\barchitecture\b",
                r"\bsystem\s+design\b",
                r"\bmicroservices?\b",
                r"\bscalab(?:le|ility)\b",
                r"\bdistributed\s+system\b",
                r"\bdatabase\s+design\b",
                r"\bapi\s+architecture\b",
            )
        ),
        "architecture_indicators",
    ),
    _CategoryRule(
        RequestCategory.DATA_ANALYSIS,
        tuple(
            re.compile(pattern)
            for pattern in (
                r"\bdataset\b",
                r"\bdata\s+analysis\b",
                r"\bcsv\b",
                r"\bstatistics?\b",
                r"\bcorrelation\b",
                r"\b(?:mean|median|visuali[sz]ation|trend)s?\b",
            )
        ),
        "data_analysis_indicators",
    ),
    _CategoryRule(
        RequestCategory.SUMMARIZATION,
        tuple(
            re.compile(pattern)
            for pattern in (
                r"\bsummar(?:y|ize|ise|izing|ising)\b",
                r"\bkey\s+points?\b",
                r"\b(?:shorten|brief|tl;dr)\b",
            )
        ),
        "summarization_indicators",
    ),
    _CategoryRule(
        RequestCategory.TRANSLATION,
        tuple(
            re.compile(pattern)
            for pattern in (
                r"\btranslat(?:e|ion|ing)\b",
                r"\bconvert\s+to\s+(?:english|hindi|spanish|french)\b",
            )
        ),
        "translation_indicators",
    ),
    _CategoryRule(
        RequestCategory.REASONING,
        tuple(
            re.compile(pattern)
            for pattern in (
                r"\bexplain\s+why\b",
                r"\bcompare\b",
                r"\banaly[sz]e\b",
                r"\bderive\b",
                r"\bprove\b",
                r"\breason(?:ing)?\b",
                r"\btrade[- ]?offs?\b",
            )
        ),
        "reasoning_indicators",
    ),
    _CategoryRule(
        RequestCategory.CREATIVE_WRITING,
        tuple(
            re.compile(pattern)
            for pattern in (
                r"\bstory\b",
                r"\bpoem\b",
                r"\bcreative\b",
                r"\bcharacter\b",
                r"\bdialogue\b",
                r"\bwrite\s+(?:a|an|the)\s+(?:short\s+)?(?:story|poem)\b",
            )
        ),
        "creative_writing_indicators",
    ),
    _CategoryRule(
        RequestCategory.CONVERSATION,
        tuple(
            re.compile(pattern)
            for pattern in (
                r"\b(?:hello|hi|hey|good\s+(?:morning|afternoon|evening))\b",
                r"\bhow\s+are\s+you\b",
                r"\bthanks?\b",
            )
        ),
        "conversation_indicators",
    ),
)

_QUESTION_PATTERN = re.compile(r"\?|\b(?:what|when|where|who|why|how|is|are|can|does)\b")
_TECHNICAL_PATTERN = re.compile(
    r"\b(?:api|backend|database|algorithm|code|system|software|server|function|data|model)\b"
)
_REASONING_PATTERN = re.compile(
    r"\b(?:because|therefore|compare|explain|why|trade[- ]?off|step\s+by\s+step)\b"
)
_MULTI_STEP_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+|\b(?:first|then|finally|step\s+by\s+step|multiple\s+steps?)\b"
)
_CODE_PATTERN = re.compile(r"```[\s\S]*```|\b(?:def|class|import|SELECT|const|function)\b")
_SENTENCE_PATTERN = re.compile(r"[.!?]+(?:\s|$)")


class DeterministicRequestClassifier:
    """Classifies the latest user message using bounded regex and structure rules."""

    def classify(
        self, messages: Sequence[ClassificationMessage]
    ) -> ClassificationResult:
        latest = next(
            (message for message in reversed(messages) if message.role == "user"),
            messages[-1] if messages else ClassificationMessage("user", ""),
        )
        text = latest.content.strip()
        normalized = text.lower()
        category, category_signal = self._category(normalized, text)
        matched_signals = self._signals(normalized, text, category_signal)
        score = self._score(normalized, text, matched_signals)
        level = self._level(score)
        explanation = self._explanation(category, matched_signals, level)
        return ClassificationResult(
            category=category,
            complexity_level=level,
            complexity_score=score,
            matched_signals=matched_signals,
            explanation=explanation,
        )

    @staticmethod
    def _category(text: str, original: str) -> tuple[RequestCategory, str | None]:
        for rule in _CATEGORY_RULES:
            if any(pattern.search(text) for pattern in rule.patterns):
                # A greeting with an incidental question is conversation, while
                # ordinary questions remain question_answer below.
                return rule.category, rule.signal
        if _QUESTION_PATTERN.search(text):
            return RequestCategory.QUESTION_ANSWER, "question_indicators"
        return RequestCategory.UNKNOWN, None

    @staticmethod
    def _signals(text: str, original: str, category_signal: str | None) -> tuple[str, ...]:
        signals: list[str] = []
        if category_signal:
            signals.append(category_signal)
        word_count = len(re.findall(r"\b\w+\b", text))
        if len(original) > 600 or word_count > 120:
            signals.append("long_prompt")
        if word_count > 250:
            signals.append("many_words")
        elif word_count > 10:
            signals.append("several_words")
        if len(_SENTENCE_PATTERN.findall(original)) >= 3:
            signals.append("multiple_sentences")
        if len(re.findall(r"\?", original)) >= 2:
            signals.append("multiple_questions")
        if _CODE_PATTERN.search(original):
            signals.append("code_signal")
        if _TECHNICAL_PATTERN.search(text):
            signals.append("technical_terms")
        if _REASONING_PATTERN.search(text):
            signals.append("reasoning_terms")
        if _MULTI_STEP_PATTERN.search(original):
            signals.append("multi_step")
        return tuple(dict.fromkeys(signals))

    @staticmethod
    def _score(text: str, original: str, signals: tuple[str, ...]) -> int:
        score = 5
        length = len(original)
        if length > 1000:
            score += 30
        elif length > 600:
            score += 22
        elif length > 300:
            score += 14
        elif length > 120:
            score += 7
        if "many_words" in signals:
            score += 25
        elif "several_words" in signals:
            score += 5
        if "multiple_sentences" in signals:
            score += 8
        if "multiple_questions" in signals:
            score += 5
        if "code_signal" in signals:
            score += 20
        if "technical_terms" in signals:
            score += 10
        if "reasoning_terms" in signals:
            score += 8
        if "multi_step" in signals:
            score += 12
        if any(signal.endswith("indicators") for signal in signals):
            score += 5
        return max(0, min(100, score))

    @staticmethod
    def _level(score: int) -> ComplexityLevel:
        if score <= 30:
            return ComplexityLevel.LOW
        if score <= 70:
            return ComplexityLevel.MEDIUM
        return ComplexityLevel.HIGH

    @staticmethod
    def _explanation(
        category: RequestCategory,
        signals: tuple[str, ...],
        level: ComplexityLevel,
    ) -> str:
        labels = {
            RequestCategory.QUESTION_ANSWER: "question indicators",
            RequestCategory.CODING: "coding indicators",
            RequestCategory.DEBUGGING: "debugging indicators",
            RequestCategory.SUMMARIZATION: "summarization indicators",
            RequestCategory.TRANSLATION: "translation indicators",
            RequestCategory.REASONING: "reasoning indicators",
            RequestCategory.ARCHITECTURE_DESIGN: "architecture-design indicators",
            RequestCategory.CREATIVE_WRITING: "creative-writing indicators",
            RequestCategory.DATA_ANALYSIS: "data-analysis indicators",
            RequestCategory.CONVERSATION: "conversation indicators",
            RequestCategory.UNKNOWN: "no strong category indicators",
        }
        extras = []
        if "multi_step" in signals:
            extras.append("a multi-step structure")
        if "code_signal" in signals:
            extras.append("code structure")
        if "technical_terms" in signals:
            extras.append("technical terminology")
        if "long_prompt" in signals:
            extras.append("a long prompt")
        if extras:
            return f"Detected {labels[category]} and {', '.join(extras)}; complexity is {level.value}."
        return f"Detected {labels[category]}; complexity is {level.value}."
