# RFC: Incident support for ML entities (mlModel, mlFeature, mlFeatureTable)

- **Author:** Danish (Blast Radius, DataHub Agent Hackathon)
- **Status:** Draft for discussion

## Summary

Extend DataHub Incidents to ML entities. Today the `incidentsSummary` aspect
is registered for `dataset`, `dashboard`, `chart`, `dataFlow`, and `dataJob`
(with `service`/`aiAgent` recently added on master) — but not for `mlModel`,
`mlFeature`, or `mlFeatureTable`, and not for `schemaField`. `raiseIncident`
against an ML entity is rejected. This RFC proposes registering the incident
aspects for the ML entity family and surfacing them in the UI and GraphQL the
same way dataset incidents are surfaced today.

## Motivation — a real limitation hit by a real agent

We built an autonomous agent (Blast Radius) that detects upstream schema
changes, walks column-level lineage to the affected `mlFeature` → `mlModel`
→ `mlModelDeployment` chain, and files incidents with evidence. The natural
place for "this model's inputs are broken" is an incident **on the model**:
that is the entity whose owners must act, whose deployment must be gated, and
whose health a CI circuit breaker queries.

Because incidents cannot attach to ML entities, we (and anyone following the
documented circuit-breaker pattern for ML) must work around it:

1. File the incident on the upstream *dataset* (the feature table), and
2. Mark the model with a convention tag (`model-at-risk`), and
3. Teach every consumer (CI gates, alerting) to join those two signals back
   together.

The workaround functions, but it splits one fact across two half-facts with
different lifecycles: resolving the incident does not clear the tag, tags
carry no state machine (ACTIVE/RESOLVED), no assignees, no priority, no
timeline, and no `incidentsSummary` health badge on the model page. The
model's health in the UI says "PASS" while its inputs are on fire.

## Proposal

1. Register `incidentsSummary` (and the incident relationship aspects) for
   `mlModel`, `mlFeatureTable`, and `mlFeature` in the entity registry.
2. Accept these entity types in `raiseIncident` / `updateIncident` /
   `updateIncidentStatus` resource validation.
3. Surface the existing incidents UI (health badge, incidents tab, filters)
   on ML entity pages — the components exist for datasets and appear
   reusable.
4. Extend the `IncidentType` guidance with ML-flavored examples
   (`FIELD`-equivalent for feature quality, `CUSTOM` types such as
   `TARGET_LEAKAGE`, `TRAINING_DATA_DRIFT`), no schema change required —
   `CUSTOM` + `customType` already covers them.

## Alternatives considered

- **Status quo (dataset incident + model tag):** works, but see Motivation —
  two signals with different lifecycles for one fact, and no model-page
  health surface.
- **Structured properties on the model:** carries data but no lifecycle, UI,
  assignees, or health integration; reinvents incidents poorly.
- **Assertions on ML entities:** assertions answer "does a check pass?", not
  "a human/agent declared an operational problem with evidence and is
  tracking it to resolution."

## Compatibility & migration

Purely additive: no change to existing incident behavior on the five
supported entity types. Existing dataset-level incidents remain valid; tools
like ours would simply file on the model *in addition to* (or instead of) the
upstream dataset once support lands.

## Evidence from the field

The full working agent that motivated this RFC (incidents on feature tables +
`model-at-risk` tags + a CI gate that joins them) is public at
github.com/Danishlynx/blast-radius — see `agent/act.py` for the workaround in
code, and `gate/gate.py` for the consumer that must re-join the two signals.
