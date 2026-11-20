# Cognitive Market Intelligence (CMI)
## Architecture RFC and Canonical Evidence Contracts

**Status:** Draft RFC  
**Target:** ChartAnalyzer multi-asset research platform  
**Scope:** Architecture and data contracts only. This document does not add ingestion, storage, model execution, trading signals, or order execution.

## 1. Product boundary

ChartAnalyzer is a market-intelligence and predictive-research platform for:
- Crypto Spot
- Crypto linear perpetual futures
- US listed equities (Nasdaq / NYSE)
- Macro and cross-asset context

The product must explain what is happening, what evidence supports competing interpretations, what could happen next, how uncertain each scenario is, and how prior forecasts performed. A trading signal is an optional downstream consumer, not the product's central output.

CMI is a research and evidence layer. Every output is informational and review-only:
- `actionable = false`
- no broker or exchange order endpoint
- no account credentials or private account scope
- no automatic strategy, weight, threshold, or portfolio changes
- no automatic promotion of a forecast into a trade

## 2. Existing capabilities to reuse

Do not create parallel implementations of capabilities already present.

| Existing module | Reuse boundary |
|---|---|
| `market_data_hub.py` | Public read-only Binance USD-M, Binance Spot and Coinbase Exchange Spot adapters; normalized OHLCV and provider provenance. |
| `funding_open_interest.py` | Provider-neutral funding/OI observations, freshness and identity checks. |
| `multi_asset/instrument.py`, `multi_asset/registry.py` | Canonical instrument identity and registry. |
| `multi_asset/market_data.py` | Provider-neutral candle normalization contracts. |
| `market_anomaly_intelligence.py` | Causal OHLCV anomaly diagnostics. Not manipulation detection or directional evidence. |
| `market_regime_intelligence.py` | Existing causal regime classification/context. |
| `regime_transition_forecasting.py` | Exploratory transition distributions and delayed OOS categorical scoring. |
| `forecast_validation.py` | Point-in-time forecast identity, delayed resolution, distributional/probability scoring and cohort separation. |
| `research_provenance.py`, `research_protocol.py`, `holdout_vault.py` | Dataset/run lineage, temporal split controls and local holdout lifecycle. |
| `strategy_validation.py` | Existing fail-closed strategy evidence gate. It must remain separate from CMI interpretation. |

The current public market-data adapters do not constitute full coverage of equities, macro, on-chain data, news, analyst research, or Iranian exchange data. Each additional source requires a separate adapter and explicit entitlement/licensing review.

## 3. Architectural rules

1. **Point-in-time truth:** distinguish when an event happened, when a source published it, when the platform first observed it, and when it became usable.
2. **Immutable evidence:** raw source records are retained by reference or content hash; normalized records are versioned and traceable to their source.
3. **No silent joins:** provider, venue, asset class, market type, quote currency, unit, instrument mapping and time semantics must match before observations can be combined.
4. **No look-ahead:** a query evaluated at time T may use only evidence whose `available_at <= T`. Later corrections are new versions, never retroactive edits to prior forecasts.
5. **Source attribution:** every extracted claim links to a source, author/publisher where known, publication time, observation time, source URL or stable source identifier, and content hash where legally permitted.
6. **Evidence is not truth:** distinguish source fact, extracted claim, analyst opinion, model inference and unverified hypothesis.
7. **Uncertainty is explicit:** missing, stale, conflicting, low-support or uncalibrated inputs lower confidence or produce an unavailable state. Never fabricate a probability.
8. **Fail closed:** invalid schema, stale critical data, unresolved identity, unknown units, broken lineage or temporal inconsistency blocks downstream synthesis.
9. **No popularity shortcut:** followers, likes, reposts and audience size are not analyst reliability scores.
10. **No LLM as source of record:** an LLM may extract, classify or summarize, but each factual claim must cite evidence and remain independently inspectable.

## 4. Logical architecture

```text
Source Adapters
  ├── Market data: spot / linear futures / equities / macro
  ├── Fundamentals: filings / earnings / tokenomics / on-chain
  ├── News & research: publishers / filings / analyst commentary
  └── Human signals: public discussion and sentiment sources
          |
          v
Ingestion & Source Governance
  ├── entitlement, licensing, rate limits and source policy
  ├── raw payload reference + content hash
  ├── timestamp normalization and identity resolution
  └── schema validation, freshness and duplicate detection
          |
          v
Canonical Evidence Layer
  ├── Instrument / Entity / Event / Claim
  ├── Observation / Source / Author
  ├── Temporal validity and provenance
  └── Contradiction and confidence metadata
          |
          +---------------------------+
          |                           |
          v                           v
Fundamental & Macro             Human / Narrative
Intelligence                    Intelligence
          |                           |
          +-------------+-------------+
                        v
              Knowledge & Causal Layer
              ├── entity/event graph
              ├── causal hypotheses
              ├── consensus / disagreement
              └── thesis state and invalidation
                        |
                        v
              Predictive Intelligence
              ├── event impact scenarios
              ├── narrative lifecycle
              ├── regime transition context
              └── calibrated forecast journal
                        |
                        v
              Intelligence Synthesis
              ├── supporting evidence
              ├── counter-evidence
              ├── uncertainty and limitations
              ├── triggers / invalidation
              └── review-only report
```

## 5. Canonical data contracts

The initial implementation should use strict Pydantic models (or equivalent validated immutable Python contracts). The following fields are normative, not a requirement to create one giant universal model.

### 5.1 SourceReference

Required:
- `source_id`: stable internal identifier
- `provider`: canonical provider key
- `source_type`: exchange, regulator, issuer, news, research, social, on_chain, macro_series, other
- `source_uri`: canonical URL or provider-native identifier
- `publisher` and `author`: nullable, never guessed
- `published_at`: nullable UTC timestamp
- `first_seen_at`: UTC timestamp assigned by our ingestion system
- `retrieved_at`: UTC timestamp for this retrieval
- `content_hash`: hash of the exact retained payload when permitted
- `license_policy`: policy identifier and permitted use
- `trust_tier`: documented source classification, not a truth score

### 5.2 EvidenceRecord

Required:
- `evidence_id`: deterministic ID derived from canonical identity and source version
- `evidence_type`: observation, filing_fact, reported_fact, analyst_opinion, extracted_claim, model_inference, hypothesis
- `entity_ids`: one or more resolved entity identifiers
- `instrument_id`: nullable canonical instrument reference
- `event_id`: nullable event reference
- `claim`: concise normalized statement or typed value
- `value`, `unit`, `currency`: typed nullable fields; no implicit unit conversion
- `valid_time_start`, `valid_time_end`: when the fact applies, if known
- `published_at`, `first_seen_at`, `available_at`, `retrieved_at`
- `source_reference_id`
- `extraction_method`, `extractor_version`: nullable for direct structured observations
- `confidence`: nullable and method-specific; not interchangeable with forecast probability
- `supersedes_evidence_id`: nullable link for corrected or superseding evidence

### 5.3 EntityRecord and EntityAlias

Entity identity is distinct from venue-specific instrument identity. The existing
`multi_asset.Instrument` and `MarketRegistry` remain authoritative for
venue, symbol and market-type instrument mapping. CMI entity records may refer
to an existing `instrument_id`, but must not create a competing instrument
registry.

Entity aliases are namespaced and may be provider-scoped and valid-time-bounded.
Resolution is exact-match only in the initial implementation:
- one eligible match: resolved;
- multiple eligible matches: ambiguous, with all candidate IDs retained;
- no eligible match: unresolved.
Fuzzy matching may suggest candidates in a future research-only layer, but it
must never silently merge entities or override canonical instrument identity.
Ticker strings are not globally unique. Venue/provider namespace and market
type must be retained where applicable.

### 5.4 ClaimRecord

A claim is a source-grounded statement, not an independent fact. It carries:
- canonical subject entity ID, predicate and normalized object text/value;
- modality: fact, opinion, hypothesis or forecast;
- stance: supports, opposes, neutral or uncertain;
- valid-time bounds and point-in-time `available_at`;
- one or more canonical evidence IDs;
- extraction method/version and optional method-specific confidence;
- deterministic claim ID.

A claim registry must reject unknown subjects, missing evidence, duplicate
evidence links, invalid confidence, and any claim whose `available_at`
precedes the availability of cited evidence. Extraction itself is a separate
stage: schema validation must not be mistaken for semantic verification.

### 5.5 EventRecord

Required:
- `event_id`, `event_type`, `entities`
- `occurred_at`, `announced_at`, `first_seen_at`, `available_at`
- `expected_value`, `actual_value`, `unit`, `currency` where applicable
- `source_reference_ids`
- `status`: scheduled, preliminary, confirmed, revised, cancelled, disputed
- `revision_of`: nullable prior event version

An event's impact must be evaluated relative to information available before the event, not with revised or post-event data inserted into the prior context.

### 5.6 Claim and contradiction

A claim is an attributed statement, not an unqualified fact:
- subject/entity, predicate, object/value and unit
- stance: supports, opposes, neutral, uncertain
- claim type: fact, opinion, forecast, causal hypothesis
- source reference and source timestamps
- evidence links and extraction version

Contradictions must preserve both claims and explain the conflict dimension (value, time, entity mapping, definition, unit, scope or interpretation). Do not resolve a conflict by silently selecting the most popular or most recent source.

### 5.7 Forecast linkage

CMI forecasts should reuse the existing `ForecastRecord` / `forecast_validation.py` journal wherever semantics match. New forecast types must carry:
- instrument/entity scope and market context
- issue time and data cutoff time
- target time or explicit resolution condition
- model and prompt/extractor version where relevant
- dataset/evidence fingerprint
- forecast horizon and target definition
- probability semantics and calibration status
- supporting and counter-evidence IDs
- invalidation conditions

Do not pool forecast metrics across different instruments, asset classes, horizons, targets, models or incompatible event cohorts.

## 6. Time model and point-in-time query

All persisted instants are timezone-aware UTC. Preserve the original source timezone/offset and raw timestamp string when supplied.

The system must not collapse these distinct timestamps:
- `occurred_at`: when the underlying event occurred
- `published_at`: when the source says it published the information
- `first_seen_at`: first platform observation
- `available_at`: earliest time the evidence is permitted to influence a forecast
- `retrieved_at`: time of the specific fetch
- `valid_time`: period in which the fact applies

For historical simulation, `available_at` is the governing cutoff. If it cannot be established, the record is excluded from point-in-time evaluation or explicitly marked non-usable. A current web page containing historical information is not proof that the information was available historically.

## 7. Fundamental intelligence domains

### Crypto
Token supply and unlock schedules, emissions, treasury, governance, protocol usage, developer activity, chain activity, exchange flows, concentration and on-chain activity. Every metric needs chain, contract/token identity, unit, block/time reference, provider and methodology.

### Equities
Issuer identity, filings, reported financial periods, revenue, margins, EPS, cash flow, debt, liquidity, guidance, corporate actions, insider and institutional disclosures. Preserve fiscal period, filing/publication time, restatement status, accounting basis and currency. Use primary filings where available.

### Macro
Economic series, release calendars, consensus estimates, actual releases, revisions, central-bank decisions, rates, yields, FX and policy events. Store vintage/revision history and release availability time; never replace the historical vintage with today's revised series.

Fundamental values should not be converted into a universal bullish/bearish score without a documented causal hypothesis and validation.

## 8. Human, sentiment and narrative intelligence

The sentiment layer may classify emotion, stance, uncertainty, sarcasm, irony, exaggeration and crowd behavior, but must preserve:
- original source and timestamp provenance
- language and translation status
- deduplication and repost/quote relationships
- bot/spam/coordination uncertainty
- entity and instrument mapping confidence
- model/version and classification uncertainty

Separate:
- sentiment volume (how much discussion exists)
- sentiment direction (stance)
- emotion (fear, excitement, frustration, hope, uncertainty)
- attention velocity (change in volume over time)
- narrative prevalence (share of eligible sources expressing a theme)

Do not infer an individual's identity or private traits. Public social data must comply with provider terms and applicable law. Popularity is not predictive skill.

A narrative lifecycle can use: detected, emerging, expanding, contested, widely priced, saturated, decaying, invalidated. These are research labels; transitions require timestamped evidence and must not be treated as deterministic market states.

## 9. Knowledge graph and causal hypotheses

Initial graph node types: Entity, Instrument, Event, Source, Claim, Evidence, Analyst, Narrative, Forecast, Thesis.

Edges must be typed and time-bounded, e.g.:
- issued_by, affects, references, supports, contradicts, reports, owns, depends_on, precedes
- hypothesized_cause_of (explicitly a hypothesis, never silently upgraded to fact)
- forecasts, invalidates, supersedes

Every edge needs provenance, creation time and (where relevant) valid time. Avoid asserting causation from correlation or temporal proximity alone.

## 10. Intelligence report contract

A report should include:
1. Subject, market and as-of timestamp.
2. Data coverage, source freshness and missing domains.
3. Confirmed observations separated from claims and hypotheses.
4. Bullish, neutral and bearish scenarios where justified.
5. Evidence for and against each scenario, with source references.
6. Event/narrative/regime context and alternative explanations.
7. Uncertainty, sample support and calibration status.
8. Triggers, invalidation conditions and review horizon.
9. Historical forecast performance for a comparable cohort, if sufficient.
10. Explicit limitations and `actionable: false`.

If evidence is insufficient or contradictory, the correct result is `INSUFFICIENT_EVIDENCE`, not a forced directional conclusion.

## 11. Storage and service boundaries

Recommended progression:
- Phase 1: validated Pydantic contracts and deterministic JSON fixtures; no new database dependency.
- Phase 2: append-only relational persistence for source, evidence, event, claim and forecast references; PostgreSQL is the target when deployment needs justify it.
- Phase 3: full-text and vector retrieval as derived indexes, always traceable to canonical evidence IDs.
- Phase 4: graph projection built from canonical relational records; the graph is not a second source of truth.

Separate services/modules by responsibility: adapters, ingestion, identity resolution, evidence normalization, domain analysis, forecast evaluation and report synthesis. Do not put network calls, LLM prompts, persistence and scoring in one module.

## 12. Security, licensing and operational controls

- Read-only API scopes only; never request trading or withdrawal permissions.
- Keep credentials outside source control and redact secrets from logs/reports.
- Enforce per-provider timeouts, bounded retries, rate limits and circuit-breaking.
- Pin allowed hosts and use HTTPS for network adapters.
- Respect source licensing, redistribution, retention and attribution requirements.
- Store only the minimum social/user data permitted by source terms.
- Treat documents and web content as untrusted input; defend against prompt injection and malicious embedded instructions.
- LLM output is untrusted until schema-validated and evidence-linked.
- Do not expose local dashboard/LAN services to the public internet without an independently reviewed security boundary.

## 13. Delivery roadmap

| Stage | Deliverable | Exit condition |
|---|---|---|
| CMI-01 | This architecture RFC and canonical contracts | Responsibilities, temporal model, evidence semantics and safety boundaries reviewed |
| CMI-02 | Source registry, entity resolution, claim contracts and evidence validation | Deterministic IDs, exact/ambiguous identity outcomes, evidence-linked claims, provenance and point-in-time tests |
| CMI-03 | Fundamental and macro adapters | Primary-source provenance, revisions/vintages, unit and availability-time tests |
| CMI-04 | News/research ingestion and claim extraction | Every claim source-linked; prompt-injection and timestamp tests |
| CMI-05 | Human sentiment and narrative lifecycle | Deduplication, language/model provenance and uncertainty tests |
| CMI-06 | Knowledge graph and contradiction engine | Typed, provenance-backed, time-aware edges and conflict preservation |
| CMI-07 | Event impact and cross-asset hypotheses | Event surprise versus prior consensus; no look-ahead; historical cohort evaluation |
| CMI-08 | Scenario synthesis and forecast evaluation | Reuse forecast journal, delayed resolution and calibration by comparable cohorts |

Each stage is a separate reviewable change. No large-bang integration and no silent wiring into the legacy `/api/analyze` path.

## 14. Acceptance criteria for this RFC

- Existing modules are reused rather than duplicated.
- Crypto spot, linear futures, equities and macro data remain explicitly typed.
- Point-in-time availability is defined independently from publication and retrieval time.
- Facts, opinions, model inferences and hypotheses are distinguishable.
- Contradictions and source revisions remain auditable.
- Forecast evaluation is delayed, cohort-safe and linked to the existing journal.
- Insufficient or stale evidence fails closed.
- All CMI outputs remain review-only and non-actionable.
- No runtime behavior, signal logic, trading gate, user configuration or dependency changes are included in this RFC.

---
**Decision requested:** review this RFC before implementing CMI-02. The next implementation should begin with the smallest validated evidence contract and tests, not with an LLM agent or a large ingestion framework.
