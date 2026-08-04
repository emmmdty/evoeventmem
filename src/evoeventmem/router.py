from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

POLICY_NAME = "query-router.rules.v1"


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
    has_entity: bool = False
    entity_count: int = Field(default=0, ge=0)
    temporal_cue_count: int = Field(default=0, ge=0)


class QueryRoutingDecision(BaseModel):
    query: str
    intent: QueryIntent
    confidence: float = Field(ge=0.0, le=1.0)
    features: QueryFeatures
    rule_hits: list[str] = Field(default_factory=list)
    reason: str
    policy_name: str = POLICY_NAME


class QueryRouter:
    """Deterministic rules-first query router with transparent features."""

    POLICY_NAME = POLICY_NAME
    MIN_COMMIT_CONFIDENCE = 0.5

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
    _TEMPORAL_RE = re.compile(
        r"\b(january|february|march|april|may|june|july|august|september|october|"
        r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b|"
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|"
        r"thu|fri|sat|sun)\b|"
        r"\b20\d{2}\b|\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(am|pm)\b|"
        r"yesterday|today|tomorrow|last week|last month|last year|next week|next month|"
        r"\d+\s?(days?|weeks?|months?|years?)\s?ago|\d+\s?(minutes?|hours?|days?|weeks?)\b|"
        r"how long (did|does|has)|when (did|was|were|will|do)|at what time|"
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
    _FEATURE_WEIGHTS = {
        "is_chit_chat": 0.6,
        "has_fact_cue": 0.3,
        "has_temporal_cue": 0.45,
        "has_relation_cue": 0.4,
        "has_episodic_cue": 0.5,
        "has_procedure_cue": 0.55,
        "has_entity": 0.2,
    }

    def route(self, query: str) -> QueryRoutingDecision:
        normalized = " ".join(query.split())
        if not normalized:
            return QueryRoutingDecision(
                query=query,
                intent=QueryIntent.HYBRID,
                confidence=0.0,
                features=QueryFeatures(),
                rule_hits=["empty_query"],
                reason="Empty query has no routing evidence.",
                policy_name=self.POLICY_NAME,
            )
        features = self.extract_features(normalized)
        intent, rule_hits, reason = self._apply_rules(features)
        confidence = self._confidence(features)
        if (
            intent is not QueryIntent.HYBRID
            and intent is not QueryIntent.NO_MEMORY
            and confidence < self.MIN_COMMIT_CONFIDENCE
        ):
            intent = QueryIntent.HYBRID
            rule_hits = [*rule_hits, "low_confidence_fallback"]
            reason = "No rule matched with enough confidence; falling back to hybrid retrieval."
        return QueryRoutingDecision(
            query=query,
            intent=intent,
            confidence=confidence,
            features=features,
            rule_hits=rule_hits,
            reason=reason,
            policy_name=self.POLICY_NAME,
        )

    def extract_features(self, query: str) -> QueryFeatures:
        temporal_matches = self._TEMPORAL_RE.findall(query)
        is_formulaic = bool(self._CHIT_CHAT_RE.match(query))
        return QueryFeatures(
            is_chit_chat=is_formulaic or bool(self._CHIT_CHAT_PREFIX_RE.match(query)),
            is_formulaic_chit_chat=is_formulaic,
            has_fact_cue=bool(self._FACT_RE.search(query)),
            has_temporal_cue=bool(temporal_matches),
            has_relation_cue=bool(self._RELATION_RE.search(query)),
            has_episodic_cue=bool(self._EPISODIC_RE.search(query)),
            has_procedure_cue=bool(self._PROCEDURE_RE.search(query)),
            has_entity=self._has_entity(query),
            entity_count=self._entity_count(query),
            temporal_cue_count=len(temporal_matches),
        )

    def _apply_rules(
        self,
        features: QueryFeatures,
    ) -> tuple[QueryIntent, list[str], str]:
        if features.is_formulaic_chit_chat:
            return (
                QueryIntent.NO_MEMORY,
                ["formulaic_chit_chat_rule"],
                "Query is a formulaic social phrase with no memory demand.",
            )
        if features.is_chit_chat and not self._has_memory_cue(features):
            return (
                QueryIntent.NO_MEMORY,
                ["chit_chat_rule"],
                "Query is chit-chat with no memory demand.",
            )
        if features.has_procedure_cue:
            return (
                QueryIntent.PROCEDURAL,
                ["procedure_rule"],
                "Query asks for a procedure or how-to steps.",
            )
        if features.has_episodic_cue:
            return (
                QueryIntent.EPISODIC,
                ["episodic_rule"],
                "Query recalls a past episode or prior conversation.",
            )
        if features.has_temporal_cue and features.temporal_cue_count >= 2:
            return (
                QueryIntent.TEMPORAL,
                ["temporal_rule", "multiple_temporal_cues"],
                "Query contains multiple temporal cues and orders events in time.",
            )
        if features.has_relation_cue:
            return (
                QueryIntent.GRAPH,
                ["relation_rule"],
                "Query asks about entity relationships or connections.",
            )
        if features.has_fact_cue:
            return (
                QueryIntent.SEMANTIC,
                ["fact_rule"],
                "Query looks up a semantic fact or attribute.",
            )
        if features.has_temporal_cue:
            return (
                QueryIntent.TEMPORAL,
                ["temporal_rule"],
                "Query anchors on a temporal expression.",
            )
        if not features.has_entity and not self._has_memory_cue(features):
            return (
                QueryIntent.HYBRID,
                ["no_entity_no_memory_cue"],
                "Query has neither an entity nor a memory cue; hybrid fallback is required.",
            )
        return (
            QueryIntent.HYBRID,
            ["unsupported_intent"],
            "No rule matched the query; hybrid fallback is required.",
        )

    def _confidence(self, features: QueryFeatures) -> float:
        score = 0.1
        for feature, weight in self._FEATURE_WEIGHTS.items():
            if getattr(features, feature):
                score += weight
        return min(1.0, score)

    def _has_memory_cue(self, features: QueryFeatures) -> bool:
        return any(
            (
                features.has_fact_cue,
                features.has_temporal_cue,
                features.has_relation_cue,
                features.has_episodic_cue,
                features.has_procedure_cue,
            )
        )

    def _entity_count(self, query: str) -> int:
        tokens = query.split()
        first_token = tokens[0].strip(".,!?;:'\"").casefold()
        entity_count = 0
        previous = ""
        for match in self._ENTITY_RE.findall(query):
            match = match.strip()
            if not match or match == previous:
                continue
            previous = match
            first_word = match.split()[0]
            if match == "I" or first_word == "I":
                continue
            if first_word.casefold() in self._ENTITY_STOPWORDS:
                continue
            if match.casefold() == first_token and first_token in self._QUESTION_STARTWORDS:
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

    def route(self, query: str) -> QueryRoutingDecision:
        decision = self._router.route(query)
        self._decisions.append(decision)
        return decision

    def list_decisions(self) -> list[QueryRoutingDecision]:
        return list(self._decisions)

    def export_jsonl(self) -> list[dict[str, object]]:
        return [decision.model_dump(mode="json") for decision in self._decisions]


__all__ = [
    "POLICY_NAME",
    "QueryFeatures",
    "QueryIntent",
    "QueryRouter",
    "QueryRouterService",
    "QueryRoutingDecision",
]
