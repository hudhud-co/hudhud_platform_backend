# ADR-NNNN: Title

- **Status:** proposed
- **Date:** YYYY-MM-DD
- **Deciders:** (names or roles)

Label every material claim as evidence, proposal, decision, assumption, or
unresolved policy. Do not invent business policy. Do not treat a suggested
deployable count as an architectural fact.

## Context

What is the issue or decision driver? What constraints apply? Cite evidence
(paths, SHAs, audits) separately from proposals.

## Options

| Option | Summary | Trade-offs |
|--------|---------|------------|
| ... | ... | ... |

## Decision drivers

Which constraints dominate (invariants, operational cost, migration risk,
team size, security)? Rank them.

## Decision

What is the change that is being proposed or enacted? If policy remains
unresolved, keep **Status: proposed** and list blockers under Unresolved
questions. Do not mark `accepted` without named deciders.

## Consequences

### Positive

- ...

### Negative

- ...

### Neutral

- ...

## Migration impact

Schema, cutover, one-writer direction, credential changes, and consumer
compatibility. Bidirectional dual-write is forbidden.

## Observability

Logs, traces, metrics, and correlation (`traceparent`, `correlation_id`)
needed to operate this decision.

## Security

Identity, least privilege, secret scope, and data classification impact.

## Rollback

How to revert or forward-repair. Irreversible facts (for example physical
delivery) must be named.

## Unresolved questions

- ...

## Alternatives Considered

| Alternative | Why rejected |
|-------------|--------------|
| ... | ... |

## References

- Related ADRs:
- Legacy evidence:
- Platform invariants: `architecture/invariants.md`
- Service boundaries: `architecture/service-boundaries.yaml`
