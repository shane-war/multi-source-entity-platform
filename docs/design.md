# Design notes: explainable reconciliation

## The problem

Applications need one useful view of a supplier, but ingestion must not erase disagreements between source systems. This example separates three concerns: source contracts, identity, and attribute selection.

## Data contract

A source record has a source, external ID, explicit supplier ID, positive integer revision, and nullable attributes. Profiles map source field names to canonical names. The pair `(source, external_id)` identifies the record. A source revision must have immutable content. Changing the canonical identity requires review outside ingestion.

The reconciliation view uses source authority and a deterministic external-ID tie-breaker. It retains all current non-null candidates. Comparison is exact string comparison; text normalization is deliberately not an identity-resolution heuristic.

## Failure behavior

Validation happens before writes. Revision collisions discovered during ingestion roll back the whole batch. A repeated record is a no-op; an older revision is counted as stale. Source records and accepted-ingestion events commit together.

## Extension path

1. Version mapping profiles and selection policies so results can identify the policy used.
2. Retain immutable source payload revisions for historical reconstruction.
3. Add tombstones and source retirement rules.
4. Introduce reviewed identity links and measure matching precision before automatic linking.
5. Move persistence to PostgreSQL when multi-writer requirements justify it; preserve transaction tests.

## Interview discussion

Explain why authority and freshness solve different problems, why a missing value is not a deletion, and why a unified entity should not conceal source disagreement. These are the central decisions demonstrated by this project.
