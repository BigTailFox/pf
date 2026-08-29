# PF Agent Instructions

## Engineering workflow

- For a substantial product, architecture, schema, or cross-contract change, establish and obtain
  acceptance for the normative Design before editing production code. Then create a durable
  implementation Plan before implementation begins.
- The Plan must map every Design acceptance criterion to ordered implementation slices,
  interface/ownership migrations, documentation and generated-artifact work, tests, and evidence
  slots.
- Keep the Plan current during implementation: record actions, decisions and deviations,
  conclusions, and exact validation commands and results. Before completion, audit every acceptance
  criterion and reconcile Design and Plan status. For a temporary migration Design, absorb its stable
  rules into the current owner documents and archive the Design and Plan in the same completed change.

## Contract evolution

- PF is pre-release. When implementing an accepted contract change, treat the target contract as the
  only compatibility target and replace affected interfaces, implementations, schemas, documentation,
  and tests cleanly. Add compatibility layers, aliases, migrations, or dual-read/write behavior only
  when the user explicitly requests them.
- Test the current contract through public seams with positive semantic assertions. Temporary migration
  checks may prove that obsolete behavior disappeared, but remove them before delivery. Retain negative
  tests only for error or safety behavior required by the current contract, not to enumerate obsolete
  syntax or historical contract variants.
