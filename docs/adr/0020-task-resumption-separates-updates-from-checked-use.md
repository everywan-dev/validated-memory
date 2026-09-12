# Task resumption separates available updates from checked use

Task resumption imports explicitly supplied compatible capsules through individual
existing transactions, then observes historical checked use at the requested task
scope. A read-only report keeps known correction work, incomplete analysis and
unverified external freshness separate: a successful check cannot imply that all
publishers were contacted or that a different task scope is eligible. This avoids
adding provisional history or batch-transaction semantics to retained schema 3;
agent-managed routes and handles remain workflow state, while semantic decisions
continue through attributed incorporation and review.
