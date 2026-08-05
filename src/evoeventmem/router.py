from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from evoeventmem.core.ports import EntityLexicon

POLICY_NAME = "query-router.rules.v1"


class TemporalOperator(StrEnum):
    NONE = "none"
    AT = "at"
    BEFORE = "before"
    AFTER = "after"
    BETWEEN = "between"
    EARLIEST = "earliest"
    LATEST = "latest"
    SEQUENCE = "sequence"
    DURATION = "duration"


class TemporalConstraint(BaseModel):
    """Explicit deterministic temporal constraint parsed from a query.

    ``operator`` is independent of answer intent: a query such as "When did
    Caroline move?" has temporal *intent* but no recency constraint
    (``operator == NONE``) and must not implicitly favor the latest memory.
    """

    operator: TemporalOperator
    lower_bound_utc: datetime | None = None
    upper_bound_utc: datetime | None = None
    matched_spans: list[str] = Field(default_factory=list)
    rule_hits: list[str] = Field(default_factory=list)
    reason: str = ""

    @property
    def is_constrained(self) -> bool:
        return self.operator is not TemporalOperator.NONE


class QueryIntent(StrEnum):
    NO_MEMORY = "no-memory"
    SEMANTIC = "semantic"
    TEMPORAL = "temporal"
    GRAPH = "graph"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"
    HYBRID = "hybrid"


class QueryFeatures(BaseModel):
    is_chit_chat: bool = False
    is_formulaic_chit_chat: bool = False
    has_fact_cue: bool = False
    has_temporal_cue: bool = False
    has_relation_cue: bool = False
    has_episodic_cue: bool = False
    has_procedure_cue: bool = False
    has_name_phrase: bool = False
    has_entity: bool = False
    entity_count: int = Field(default=0, ge=0)
    temporal_cue_count: int = Field(default=0, ge=0)
    strong_temporal_count: int = Field(default=0, ge=0)
    weak_temporal_count: int = Field(default=0, ge=0)


class QueryRoutingDecision(BaseModel):
    query: str
    intent: QueryIntent
    confidence: float = Field(ge=0.0, le=1.0)
    features: QueryFeatures
    temporal_constraint: TemporalConstraint = Field(
        default_factory=lambda: TemporalConstraint(operator=TemporalOperator.NONE)
    )
    rule_hits: list[str] = Field(default_factory=list)
    reason: str
    policy_name: str = POLICY_NAME


class QueryRouter:
    """Deterministic query router that arbitrates intents by cue scores.

    Confidence is bounded evidence strength (0.1 + winner score, capped at
    1.0); it is an ordinal ranking signal, not a calibrated probability.
    """

    POLICY_NAME = POLICY_NAME
    MIN_COMMIT_CONFIDENCE = 0.5

    _INTENT_PRIORITY = {
        QueryIntent.PROCEDURAL: 0,
        QueryIntent.EPISODIC: 1,
        QueryIntent.TEMPORAL: 2,
        QueryIntent.GRAPH: 3,
        QueryIntent.SEMANTIC: 4,
    }
    _INTENT_REASONS = {
        QueryIntent.PROCEDURAL: "Query asks for a procedure or how-to steps.",
        QueryIntent.EPISODIC: "Query recalls a past episode or prior conversation.",
        QueryIntent.TEMPORAL: "Query anchors on temporal expressions.",
        QueryIntent.GRAPH: "Query asks about entity relationships or connections.",
        QueryIntent.SEMANTIC: "Query looks up a semantic fact or attribute.",
    }

    _CHIT_CHAT_RE = re.compile(
        r"^(hi|hi there|hello|hey|thanks|thanks a lot|thanks so much|"
        r"thank you|thank you so much|thank you very much|"
        r"how are you|how are you doing|nice to meet you|good morning|"
        r"good afternoon|good evening|goodbye|bye|see you|see you later|"
        r"great|awesome|ok|okay|sure|got it|no problem|you're welcome|"
        r"yes|no|sounds good|makes sense|what's up)\W*$",
        re.IGNORECASE,
    )
    _CHIT_CHAT_PREFIX_RE = re.compile(r"^(thanks|thank you)\b", re.IGNORECASE)
    _PROCEDURE_RE = re.compile(
        r"how (do i|to|would i|can i)|steps? to|step by step|procedure|"
        r"instructions?|workflow|process for|best way to|guide me|walk me through",
        re.IGNORECASE,
    )
    _TEMPORAL_STRONG_RE = re.compile(
        r"\b(january|february|march|april|may|june|july|august|september|october|"
        r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b|"
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|"
        r"thu|fri|sat|sun)\b|"
        r"\b20\d{2}\b|\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(am|pm)\b|"
        r"\b(last|next|this) (week|month|year)\b|"
        r"\d+\s?(days?|weeks?|months?|years?)\s?ago|"
        r"how long (did|does|has)|when (did|was|were|will|do)|at what time|what time",
        re.IGNORECASE,
    )
    _TEMPORAL_WEAK_RE = re.compile(
        r"yesterday|today|tomorrow|"
        r"before|after|during|since|until|first|last|finally|then|"
        r"earlier|later|recently|previously|afterwards|subsequently|eventually|"
        r"in the beginning|at first|at the time|as of now|currently|now|"
        r"meanwhile|while|back then|that time|the other day",
        re.IGNORECASE,
    )
    _EPISODIC_RE = re.compile(
        r"did i (tell|mention)|i (told|mentioned) you|you (told|mentioned|said)|"
        r"we (discussed|talked|spoke) about|in our conversation|during our (chat|meeting|call)|"
        r"do you (remember|recall)|you remember|what happened (during|at|when)|"
        r"experience|episode|the trip|my trip|that time",
        re.IGNORECASE,
    )
    _RELATION_RE = re.compile(
        r"relat(ed|ionship|ion)|know each other|connection|connected|"
        r"colleague|colleagues|friend|friends|associated with|works with|"
        r"interacts with|team up|partner|manager of|report(s|ed) to",
        re.IGNORECASE,
    )
    _FACT_RE = re.compile(
        r"what is|what's|what are|what was|who is|who's|who was|which|"
        r"where (is|does|do)|how old|what kind of|what type of|"
        r"favorite|prefers?|preference|likes?|dislikes?|lives in|works as|"
        r"based in|interested in|age|birthday|hometown|name of|do you know|"
        r"is it|does .+ prefer|hobby|hobbies",
        re.IGNORECASE,
    )
    _NAME_PHRASE_RE = re.compile(
        r"\b(first|last|middle|full|legal)\s+names?\b",
        re.IGNORECASE,
    )
    _ENTITY_RE = re.compile(
        r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b|\b(?:GPT|API|AWS|SQL|CLI|CI|CD|OS|JSON|UI)\b",
        re.UNICODE,
    )
    _ENTITY_STOPWORDS = frozenset(
        {
            "hi",
            "hello",
            "hey",
            "thanks",
            "thank",
            "how",
            "are",
            "you",
            "did",
            "what",
            "when",
            "who",
            "which",
            "where",
            "why",
            "would",
            "could",
            "can",
            "yes",
            "no",
            "ok",
            "sure",
            "great",
            "good",
            "best",
            "first",
            "last",
            "later",
            "earlier",
            "recently",
            "today",
            "tomorrow",
            "yesterday",
            "now",
            "then",
            "while",
            "during",
            "until",
            "before",
            "after",
            "eventually",
            "currently",
        }
    )
    _QUESTION_STARTWORDS = frozenset(
        {
            "what",
            "who",
            "when",
            "where",
            "why",
            "how",
            "which",
            "do",
            "does",
            "did",
            "is",
            "are",
            "was",
            "were",
            "can",
            "could",
            "would",
            "will",
            "should",
            "have",
            "has",
        }
    )

    def __init__(
        self,
        *,
        entity_lexicon: EntityLexicon | None = None,
        reference_time: datetime | None = None,
    ) -> None:
        self._lexicon = entity_lexicon
        self._default_reference_time = reference_time

    def route(
        self,
        query: str,
        *,
        reference_time: datetime | None = None,
    ) -> QueryRoutingDecision:
        normalized = " ".join(query.split())
        if not normalized:
            return QueryRoutingDecision(
                query=query,
                intent=QueryIntent.HYBRID,
                confidence=0.0,
                features=QueryFeatures(),
                temporal_constraint=self._parse_temporal_constraint(
                    normalized,
                    reference_time,
                ),
                rule_hits=["empty_query"],
                reason="Empty query has no routing evidence.",
                policy_name=self.POLICY_NAME,
            )
        features = self.extract_features(normalized)
        intent, score, rule_hits, reason = self._apply_rules(features)
        confidence = min(1.0, 0.1 + score)
        if (
            intent is not QueryIntent.HYBRID
            and intent is not QueryIntent.NO_MEMORY
            and confidence < self.MIN_COMMIT_CONFIDENCE
        ):
            intent = QueryIntent.HYBRID
            rule_hits = [*rule_hits, "low_confidence_fallback"]
            reason = "No rule matched with enough confidence; falling back to hybrid retrieval."
        temporal_constraint = self._parse_temporal_constraint(
            normalized,
            reference_time,
        )
        return QueryRoutingDecision(
            query=query,
            intent=intent,
            confidence=confidence,
            features=features,
            temporal_constraint=temporal_constraint,
            rule_hits=rule_hits,
            reason=reason,
            policy_name=self.POLICY_NAME,
        )

    def _parse_temporal_constraint(
        self,
        query: str,
        reference_time: datetime | None,
    ) -> TemporalConstraint:
        reference = reference_time or self._default_reference_time
        constraint = _detect_temporal_constraint(query, reference)
        if constraint is None:
            return TemporalConstraint(operator=TemporalOperator.NONE)
        return constraint

    def extract_features(self, query: str) -> QueryFeatures:
        strong_temporal = self._TEMPORAL_STRONG_RE.findall(query)
        weak_temporal = self._TEMPORAL_WEAK_RE.findall(query)
        is_formulaic = bool(self._CHIT_CHAT_RE.match(query))
        return QueryFeatures(
            is_chit_chat=is_formulaic or bool(self._CHIT_CHAT_PREFIX_RE.match(query)),
            is_formulaic_chit_chat=is_formulaic,
            has_fact_cue=bool(self._FACT_RE.search(query)),
            has_temporal_cue=bool(strong_temporal or weak_temporal),
            has_relation_cue=bool(self._RELATION_RE.search(query)),
            has_episodic_cue=bool(self._EPISODIC_RE.search(query)),
            has_procedure_cue=bool(self._PROCEDURE_RE.search(query)),
            has_name_phrase=bool(self._NAME_PHRASE_RE.search(query)),
            has_entity=self._has_entity(query),
            entity_count=self._entity_count(query),
            temporal_cue_count=len(strong_temporal) + len(weak_temporal),
            strong_temporal_count=len(strong_temporal),
            weak_temporal_count=len(weak_temporal),
        )

    def _apply_rules(
        self,
        features: QueryFeatures,
    ) -> tuple[QueryIntent, float, list[str], str]:
        if features.is_formulaic_chit_chat:
            return (
                QueryIntent.NO_MEMORY,
                1.0,
                ["formulaic_chit_chat_rule"],
                "Query is a formulaic social phrase with no memory demand.",
            )
        scores = self._score_intents(features)
        rule_hits = self._matched_cue_hits(features)
        if features.is_chit_chat and not scores:
            return (
                QueryIntent.NO_MEMORY,
                0.4,
                ["chit_chat_rule"],
                "Query is chit-chat with no memory demand.",
            )
        if not scores:
            if features.has_entity:
                return (
                    QueryIntent.HYBRID,
                    0.0,
                    ["unsupported_intent"],
                    "No rule matched the query; hybrid fallback is required.",
                )
            return (
                QueryIntent.HYBRID,
                0.0,
                ["no_entity_no_memory_cue"],
                "Query has neither an entity nor a memory cue; hybrid fallback is required.",
            )
        winner = max(
            scores,
            key=lambda intent: (
                scores[intent],
                -self._INTENT_PRIORITY[intent],
            ),
        )
        winner_score = scores[winner]
        hits = [*rule_hits, f"{winner.value}_rule"]
        if winner is QueryIntent.TEMPORAL and features.temporal_cue_count >= 2:
            hits.append("multiple_temporal_cues")
        reason = (
            "Query contains multiple temporal cues and orders events in time."
            if winner is QueryIntent.TEMPORAL and features.temporal_cue_count >= 2
            else self._INTENT_REASONS[winner]
        )
        return winner, winner_score, hits, reason

    def _score_intents(self, features: QueryFeatures) -> dict[QueryIntent, float]:
        scores: dict[QueryIntent, float] = {}
        if features.has_procedure_cue:
            scores[QueryIntent.PROCEDURAL] = 0.6
        if features.has_episodic_cue:
            scores[QueryIntent.EPISODIC] = 0.6
        if features.has_temporal_cue:
            temporal_score = min(
                1.0,
                0.6 * features.strong_temporal_count
                + 0.4 * min(1, features.weak_temporal_count),
            )
            scores[QueryIntent.TEMPORAL] = temporal_score
        if features.has_relation_cue:
            scores[QueryIntent.GRAPH] = 0.5
        semantic_score = min(
            1.0,
            0.35 * int(features.has_fact_cue)
            + 0.15 * int(features.has_entity)
            + 0.3 * int(features.has_name_phrase),
        )
        if semantic_score > 0.0:
            scores[QueryIntent.SEMANTIC] = semantic_score
        return scores

    def _matched_cue_hits(self, features: QueryFeatures) -> list[str]:
        hits: list[str] = []
        cue_mapping = (
            ("has_procedure_cue", "procedure_cue"),
            ("has_episodic_cue", "episodic_cue"),
            ("has_relation_cue", "relation_cue"),
            ("has_fact_cue", "fact_cue"),
            ("has_name_phrase", "name_phrase_cue"),
            ("has_entity", "entity_cue"),
        )
        for field, hit in cue_mapping:
            if getattr(features, field):
                hits.append(hit)
        if features.strong_temporal_count:
            hits.append("strong_temporal_cue")
        if features.weak_temporal_count:
            hits.append("weak_temporal_cue")
        return hits

    def _entity_count(self, query: str) -> int:
        tokens = query.split()
        first_token = tokens[0].strip(".,!?;:'\"").casefold()
        candidates: list[str] = []
        seen: set[str] = set()
        for match in self._ENTITY_RE.findall(query):
            match = match.strip()
            if match and match not in seen:
                seen.add(match)
                candidates.append(match)
        if self._lexicon is not None:
            for token in tokens:
                cleaned = token.strip(".,!?;:'\"()[]")
                key = cleaned.casefold()
                if key and key not in seen and self._lexicon.contains(key):
                    seen.add(key)
                    candidates.append(key)
        entity_count = 0
        for candidate in candidates:
            first_word = candidate.split()[0]
            if candidate == "I" or first_word == "I":
                continue
            if first_word.casefold() in self._ENTITY_STOPWORDS:
                continue
            if candidate.casefold() == first_token and first_token in self._QUESTION_STARTWORDS:
                continue
            entity_count += 1
        return entity_count

    def _has_entity(self, query: str) -> bool:
        return self._entity_count(query) > 0


class QueryRouterService:
    """Router facade that records every decision for observability."""

    def __init__(self, router: QueryRouter | None = None) -> None:
        self._router = router or QueryRouter()
        self._decisions: list[QueryRoutingDecision] = []

    def route(self, query: str, *, reference_time: datetime | None = None) -> QueryRoutingDecision:
        decision = self._router.route(query, reference_time=reference_time)
        self._decisions.append(decision)
        return decision

    def list_decisions(self) -> list[QueryRoutingDecision]:
        return list(self._decisions)

    def export_jsonl(self) -> list[dict[str, object]]:
        return [decision.model_dump(mode="json") for decision in self._decisions]


_YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")
_AT_DATE_RE = re.compile(r"\b(20\d{2}|19\d{2})\b|in \b(20\d{2}|19\d{2})\b")
_MONTH_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\b",
    re.IGNORECASE,
)
_TO_YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b", re.IGNORECASE)
_RELATIVE_RE = re.compile(
    r"\b(last|next|this|past|previous|coming)\s+(week|month|year)\b",
    re.IGNORECASE,
)
_BEFORE_RE = re.compile(r"\bbefore\b|\bprior to\b|\bup to\b|\buntil\b", re.IGNORECASE)
_AFTER_RE = re.compile(r"\bafter\b|\bsince\b|\bfollowing\b|\bsubsequent to\b", re.IGNORECASE)
_BETWEEN_RE = re.compile(r"\bbetween\b", re.IGNORECASE)
_FIRST_RE = re.compile(r"\b(first|earliest|initial)\b", re.IGNORECASE)
_LAST_RE = re.compile(r"\b(last|most recent|latest)\b", re.IGNORECASE)
_SEQUENCE_RE = re.compile(
    r"\b(order|sequence|ordering|chronolog|first.*then|then.*after|before.*after)\b",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"\b(how long|duration|lasted?|for how long|length of time)\b",
    re.IGNORECASE,
)


def _detect_temporal_constraint(
    query: str,
    reference_time: datetime | None,
) -> TemporalConstraint | None:
    """Return a TemporalConstraint, or None when no operator applies.

    Matching is deterministic and applied in a fixed priority. ``matched_spans``
    and ``rule_hits`` record exactly which surface cues were consumed so the
    decision is observable and reproducible.
    """
    normalized = " ".join(query.lower().split())
    spans: list[str] = []
    rule_hits: list[str] = []

    def _record(rule: str, *matched: str) -> None:
        rule_hits.append(rule)
        spans.extend(s for s in matched if s)

    if _BETWEEN_RE.search(normalized):
        years = _YEAR_RE.findall(normalized)
        if len(years) >= 2:
            lower = datetime(int(years[0]), 1, 1, tzinfo=UTC)
            upper = datetime(int(years[1]), 12, 31, 23, 59, 59, tzinfo=UTC)
            _record("between_rule", "between", *years)
            return TemporalConstraint(
                operator=TemporalOperator.BETWEEN,
                lower_bound_utc=lower,
                upper_bound_utc=upper,
                matched_spans=spans,
                rule_hits=rule_hits,
                reason="Query bounds a range between two explicit dates.",
            )
    if _BEFORE_RE.search(normalized):
        year = _YEAR_RE.search(normalized)
        if year is not None:
            upper = datetime(int(year.group(1)), 12, 31, 23, 59, 59, tzinfo=UTC)
            _record("before_rule", "before", year.group(1))
            return TemporalConstraint(
                operator=TemporalOperator.BEFORE,
                upper_bound_utc=upper,
                matched_spans=spans,
                rule_hits=rule_hits,
                reason="Query bounds events before an explicit date.",
            )
    if _AFTER_RE.search(normalized):
        year = _YEAR_RE.search(normalized)
        if year is not None:
            lower = datetime(int(year.group(1)), 1, 1, tzinfo=UTC)
            _record("after_rule", "after", year.group(1))
            return TemporalConstraint(
                operator=TemporalOperator.AFTER,
                lower_bound_utc=lower,
                matched_spans=spans,
                rule_hits=rule_hits,
                reason="Query bounds events after an explicit date.",
            )
    if _AT_DATE_RE.search(normalized):
        year = _YEAR_RE.search(normalized)
        if year is not None:
            lower = datetime(int(year.group(1)), 1, 1, tzinfo=UTC)
            upper = datetime(int(year.group(1)), 12, 31, 23, 59, 59, tzinfo=UTC)
            _record("at_rule", year.group(1))
            return TemporalConstraint(
                operator=TemporalOperator.AT,
                lower_bound_utc=lower,
                upper_bound_utc=upper,
                matched_spans=spans,
                rule_hits=rule_hits,
                reason="Query anchors on an explicit date.",
            )
    if _DURATION_RE.search(normalized):
        _record("duration_rule", "how long")
        return TemporalConstraint(
            operator=TemporalOperator.DURATION,
            matched_spans=spans,
            rule_hits=rule_hits,
            reason="Query asks about elapsed time.",
        )
    if _SEQUENCE_RE.search(normalized):
        _record("sequence_rule", "order")
        return TemporalConstraint(
            operator=TemporalOperator.SEQUENCE,
            matched_spans=spans,
            rule_hits=rule_hits,
            reason="Query asks about event ordering.",
        )
    relative = _RELATIVE_RE.search(normalized)
    if relative is not None and reference_time is not None:
        unit = relative.group(2)
        span_text = relative.group(0)
        _record("relative_rule", span_text)
        delta = {"week": 7, "month": 30, "year": 365}[unit]
        lower = reference_time - timedelta(days=delta)
        return TemporalConstraint(
            operator=TemporalOperator.BETWEEN,
            lower_bound_utc=lower,
            upper_bound_utc=reference_time,
            matched_spans=spans,
            rule_hits=rule_hits,
            reason="Query bounds events relative to a UTC reference time.",
        )
    if _FIRST_RE.search(normalized):
        _record("earliest_rule", "first")
        return TemporalConstraint(
            operator=TemporalOperator.EARLIEST,
            matched_spans=spans,
            rule_hits=rule_hits,
            reason="Query asks about the earliest occurrence.",
        )
    if _LAST_RE.search(normalized):
        _record("latest_rule", "last")
        return TemporalConstraint(
            operator=TemporalOperator.LATEST,
            matched_spans=spans,
            rule_hits=rule_hits,
            reason="Query asks about the latest occurrence.",
        )
    if _BEFORE_RE.search(normalized) or _AFTER_RE.search(normalized):
        _record("temporal_relation_without_date")
        return TemporalConstraint(
            operator=TemporalOperator.NONE,
            matched_spans=spans,
            rule_hits=rule_hits,
            reason="Query mentions a temporal relation but lacks an explicit date.",
        )
    return None


__all__ = [
    "POLICY_NAME",
    "QueryFeatures",
    "QueryIntent",
    "QueryRouter",
    "QueryRouterService",
    "QueryRoutingDecision",
    "TemporalConstraint",
    "TemporalOperator",
]
