# Batch write failure recovery

## Problem

The batch write loop sits outside its projection recovery boundary. An unexpected
failure in a later item leaves earlier canonical changes on disk without projecting
them or reporting them as accepted. Invalid weights, event dates, and non-object
JSONL items also escape the per-item validation contract.

## Contract

- A malformed submitted item is rejected with its index and field; valid siblings
  continue and share one projection under the writer lock.
- An operational failure aborts the batch, restores canonical files to their first
  pre-batch contents, and reprojects the restored truth before propagating the error.
- Existing error categories remain intact, including missing predecessor errors.
- Recovery retains append-only archived evidence. It does not promise process-crash
  atomicity, empty-directory cleanup, or recovery while storage remains unavailable.
- Full projection continues to discover external file edits.

## Units

1. Document the batch validation and recovery boundary in the design index and a
   dedicated concise design note.
2. Add failing unit regressions for malformed inputs and aborts during later writes,
   including repeated paths, moves, successors, and projection failure. Add CLI
   regressions for partial success and a missing predecessor after an accepted item.
3. Extend the existing rollback boundary to include the write loop and normalize
   the identified input errors before persistence. Keep one shared write path.
4. Run focused tests, the full suite, Ruff, mypy, and an independent CLI exercise.
   Review the diff and record results before handing off for review.

## Acceptance

- Invalid weight/date/object inputs reject only their item; valid numeric weights
  remain accepted and range validation remains authoritative.
- Unexpected write failures restore earlier changes and the current item's files;
  default recall, MEMORY.md, and rebuild agree on the restored records.
- Repeated updates restore the original pre-batch bytes, including relationships
  and validity intervals; appended raw evidence remains recoverable.
- Successful batches still project once and require no explicit index sync.

## Verification

- Before implementation: 14 new unit failures and 7 CLI failures reproduced the
  missing validation/recovery boundaries. Review identified two additional UTC
  conversion overflow cases; both failed before extending timestamp validation.
- Focused batch, timestamp, and CLI suite: 81 passed.
- Full suite: 597 passed, 93.15% coverage. Ruff, mypy (69 source files), and whitespace
  checks passed.
- Independent CLI subprocess exercise verified mixed-batch rejection, missing
  predecessor rollback, recall/MEMORY.md/rebuild consistency, and automatic discovery
  of an external edit with unchanged size and restored mtime.
- Independent review found no other actionable issue; the identified date overflow
  cases are now covered and pass. No performance improvement is claimed.
