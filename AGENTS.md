# PF Agent Instructions

## Engineering workflow

- **Route by impact.** Check the current owner contract and affected public seams. For an authorized
  local fix restoring that contract, a small internal refactor, or a test/document correction, proceed
  directly through implementation and appropriate validation; no new Design, Plan, or Review is required.
  Any contract change or substantial product, architecture, schema, or cross-contract work takes the
  Design path, even when its code diff is small. Judge impact by product promises, public interfaces,
  ownership, invariants, and migration risk; touching several files or modules alone does not require it.
- **Design path.** Establish and obtain acceptance for the normative Design before editing production
  code. Once implementation is authorized, create a durable Plan and carry it through implementation,
  validation, acceptance audit, and documentation closure without separate approval for each phase.
  A review-only or Design-only request stays within that scope; Design acceptance alone is not
  implementation authorization.
- **Execution autonomy.** Choose implementation details and adjust slices within the accepted contract
  and authorized scope. Ask when a target contract must change, work must exceed that scope, or a
  material choice cannot be resolved from available evidence. Continue independent authorized work
  while that decision is pending.
- **Plan granularity.** Map every Design acceptance criterion to ordered, verifiable slices, including
  dependencies, interface/ownership migrations, documentation and generated artifacts, tests, and
  evidence slots. Reference the Design for contract rules; leave private implementation details to
  execution unless they determine feasibility or correctness. Scale Design and Plan detail to impact;
  a bounded contract change may amend its existing owner Design without creating a new Design document.
- **Progress and completion.** Update the Plan at slice boundaries and material decisions with outcomes,
  deviations, and exact validation commands and results. Keep completed work, the next step, and evidence
  paths in the Plan; retain raw output in logs. Audit every acceptance criterion and reconcile Design/Plan status;
  document completion alone does not prove behavior. For a temporary migration Design, absorb stable
  rules into current owners and archive the Design and Plan in the same completed change. For lightweight
  work, report the change, validation results, and remaining limitations in the final response.

## Execution efficiency

- **Context.** Locate the owner, then read relevant sections, implementation, and public tests. Consult
  archives for unresolved history or contract conflicts. Reuse already-read material while its contents
  remain unchanged; refresh affected files after concurrent edits or a context gap.
- **Tool output.** Search for paths and focused excerpts first. Save long output to logs; surface command
  status, concise findings, and evidence paths, expanding failures as needed. Preserve producer exit codes.
- **Exploration.** State the unknown and the evidence needed to choose, then use a focused inspection,
  reproduction, or experiment. Once evidence supports a choice, execute. Record unrelated follow-ups and
  finish when the authorized scope and required validation are complete.
- **Delegation.** Use subagents for bounded independent investigations or high-risk independent reviews
  when their expected benefit exceeds coordination and duplicated context. Keep simple work in one agent;
  delegate scope, constraints, and necessary evidence, requesting findings, paths, and unresolved questions.
  Give parallel editors disjoint ownership; independently verify findings before incorporating them.

## Engineering docs

- **Owner map** — locating the unique owner of a behavior, or adding, moving, or archiving a
  Design, Plan, Review, Concept, or Experiment: follow [docs/README.md](docs/README.md).
- **Vocabulary** — choosing or checking a PF domain term: use [CONTEXT.md](CONTEXT.md).

## Tests

- **Repository tests** prove the current contract through public seams. In-process results,
  real-process protocol, and qualification matrices are not interchangeable.
- Adding a test, choosing its marker or consumer, selecting validation, or reusing test evidence: follow
  [tests/README.md](tests/README.md).

## Contract evolution

- PF is pre-release. When implementing an accepted contract change, treat the target contract as the
  only compatibility target and replace affected interfaces, implementations, schemas, documentation,
  and tests cleanly. Add compatibility layers, aliases, migrations, or dual-read/write behavior only
  when the user explicitly requests them.
- Test the current contract through public seams with positive semantic assertions. Temporary migration
  checks may prove that obsolete behavior disappeared, but remove them before delivery. Retain negative
  tests only for error or safety behavior required by the current contract, not to enumerate obsolete
  syntax or historical contract variants.

## Run Environment

- Accessibility to network and uv cache may be blocked by the agent sandbox. Especially when running pf CLI commands
  and pf tests. Always run these works under the pf repo root out of the sandbox after checking free of risks.
