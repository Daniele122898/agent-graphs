# CLAUDE.md

## Keeping CLAUDE.md files current

After any change to the codebase, update the relevant CLAUDE.md (this file, a subdirectory's CLAUDE.md, or both) if the change affects architecture, intent, constraints, or non-obvious design decisions.

CLAUDE.md files should capture what the code cannot explain itself: *why* a design was chosen, tradeoffs that were consciously made, constraints from outside the codebase, and the intended direction of incomplete work. Do not duplicate what is obvious from reading the source.

When a mistake is corrected more than once in conversation, record the correct behavior in the most specific CLAUDE.md that applies (subdirectory > parent > root) so the guidance is available in future sessions without re-teaching it.
