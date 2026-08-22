# S6 Future Research Directions

## Priority 1: Extraction Stability (Immediate)

**Problem**: ETEC performance is entirely determined by extraction quality, which is non-deterministic.

**Approaches**:
1. **Deterministic extraction**: Replace LLM-based extraction with rule-based or hybrid extraction for critical fields (temporal, numerical)
2. **Ensemble extraction**: Run extraction 3 times, take the union of events
3. **Extraction caching**: Cache extraction results by content hash, not by run
4. **Hybrid approach**: Use raw turns for single-session, ETEC for multi-session

**Expected Impact**: Full EM from 0.16 → 0.30+ (matching vector_rag)

## Priority 2: Metric Refinement (Short-term)

**Problem**: Strict EM penalizes correct answers with extra context.

**Approaches**:
1. **Contains-match**: Gold answer contained in prediction → EM=1
2. **Token F1**: Already implemented, use as primary metric
3. **LLM-as-judge**: Use a separate LLM to evaluate answer correctness
4. **Human evaluation**: Manual validation of a sample

**Expected Impact**: Adjusted EM from 0.16 → 0.32 (full method)

## Priority 3: Router Improvement (Medium-term)

**Problem**: Router accuracy affects which method is used for each query.

**Approaches**:
1. **Learned router**: Train a classifier on query features
2. **Ensemble routing**: Use multiple routing strategies
3. **Query expansion**: Expand queries before routing
4. **Intent detection**: Better classification of query types

**Expected Impact**: Router accuracy from 60% → 80%+

## Priority 4: ETEC Threshold Tuning (Medium-term)

**Problem**: ETEC thresholds may not be optimal for all question types.

**Approaches**:
1. **Per-type thresholds**: Different thresholds for different question types
2. **Adaptive thresholds**: Adjust thresholds based on query complexity
3. **Grid search**: Systematic search over threshold space
4. **Bayesian optimization**: Use BO to find optimal thresholds

**Expected Impact**: ETEC gap from +0.00 → +0.10

## Priority 5: Scaling to Full Dataset (Long-term)

**Problem**: 50-question pilot is too small for statistical significance.

**Approaches**:
1. **Full 500-question run**: Run on the complete LongMemEval dataset
2. **Cross-validation**: K-fold cross-validation for robust estimates
3. **Bootstrap confidence intervals**: Statistical significance testing
4. **Multiple datasets**: Validate on LoCoMo, MASSIVE, etc.

**Expected Impact**: Statistical significance, generalizability

---

## Research Roadmap

```
Phase 1 (1-2 weeks): Fix extraction stability
  → Target: Full EM ≥ 0.30
  → Method: Deterministic extraction or ensemble

Phase 2 (1 week): Refine metrics
  → Target: Adjusted EM ≥ 0.40
  → Method: Contains-match or token F1

Phase 3 (2-3 weeks): Improve router
  → Target: Router accuracy ≥ 80%
  → Method: Learned classifier

Phase 4 (1-2 weeks): Tune ETEC
  → Target: ETEC gap ≥ +0.10
  → Method: Per-type thresholds

Phase 5 (1 month): Scale and validate
  → Target: Full 500-question run
  → Method: Cross-validation + multiple datasets
```

---

## Key Metrics to Track

| Metric | Current | Target | Method |
|---|---|---|---|
| Full EM (raw) | 0.16 | 0.30 | Fix extraction |
| Full EM (adjusted) | 0.32 | 0.40 | Refine metrics |
| Vector RAG EM | 0.30 | 0.35 | Router improvement |
| ETEC gap | +0.00 | +0.10 | Threshold tuning |
| Extraction stability | 0-5/10 overlap | 8+/10 overlap | Deterministic extraction |
| No-info responses | 16.2% | <5% | Better extraction |

---

## Risk Factors

1. **LLM API costs**: Full 500-question run will be expensive
2. **Time**: Each experiment takes 10+ hours
3. **Infrastructure**: SSH tunnel to embedding server is unstable
4. **Model availability**: mimo-v2.5 API may have rate limits
