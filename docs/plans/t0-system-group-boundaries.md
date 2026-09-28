# Preserve system group boundaries during T0 maintenance

## Problem

T0 clustering and spelling-based group merging treat every schema group as a topic.
For project-scoped memories and dated events, moving the directory also rewrites the
system field in the canonical file. A later scoped recall can no longer find the
memory under its original project or date.

## Contract

- Automatic directory regrouping is eligible only when the schema's group field has
  the configured source `menu`.
- System fields retain their values and canonical paths during both T0 directory
  operations, including custom schemas and field-source overrides. Empty-group
  cleanup is also restricted to menu fields.
- Existing menu clustering and spelling-based merging remain available.
- Explicit Store operations, T1 decisions, and date timestamp normalization retain
  their current behavior. This change does not infer or restore already moved files.

## Implementation units

1. Update the management design boundary and its design index entry.
2. Add failing unit regressions for project/date/custom system groups, spelling
   variants, configuration overrides, and positive menu behavior.
3. Add failing rule-only CLI Sleep regressions proving scoped recall, canonical
   paths, rebuilds, and repeated Sleep preserve the protected group boundaries.
4. Restrict both automatic directory operations using the existing field-source
   resolver. Keep the patch independent of the T0 duplicate-identity fix.
5. Run focused and full tests, lint, types, an independent CLI reproduction, and a
   read-only review. Record results before committing the isolated branch.

## Acceptance

- A crowded project or event directory retains its fields and paths after Sleep.
- Distinct system groups with spelling-normalized equivalents remain separate.
- Scoped recall returns the same matching records after Sleep and index rebuild.
- Custom system groups are protected; menu eligibility follows configuration rather
  than hardcoded field names or memory types.
- Existing upsert menu schemas still preserve bodies, evidence, and recallability
  when regrouped. The add-only regrouping follow-up is tracked separately.

## Verification

- Baseline: upstream `dad479cd`; independent of the duplicate-identity change.
- Before implementation: 10 unit regressions failed and 8 positive cases passed;
  all 3 new CLI regressions failed on the expected group changes.
- Focused Manage, schema, CLI, and sleep-store tests: 111 passed.
- Full suite: 588 passed; coverage 93.07%, above the 85% gate.
- Ruff passed; mypy passed for 69 source files; diff whitespace checks passed.
- Independent CLI subprocess run against a temporary synthetic store preserved
  12 records across four protected scopes, while 5 menu records still clustered.
  Scoped recall, rebuild, repeated rule-only Sleep, and evidence checks passed.
- Read-only review found no blocking issues. No model endpoint or real user memory
  store was involved. No latency or throughput improvement is claimed.
