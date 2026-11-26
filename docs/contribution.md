# Contribution table (Guide §11.2): must match git history
Verify with: `git shortlog -sne main`, `gh pr list --state merged --author <user> --json number | jq length`,
`gh api "search/issues?q=repo:<org>/<repo>+type:pr+reviewed-by:<user>" --jq .total_count`.

| Area | Member 1 = Dev A (Taimoor, 24i-3015) | Member 2 = Dev B (Bilal, 24i-3166) |
|---|---|---|
| Modules owned | M14, M5, M4, M6i, M6, M1 UI, M10t, M10 (serving), M13 (ops) | M11, M2, M1 gateway, M3, M9, M8, M7, M12, M13 (eval) |
| Pull requests authored / reviewed | | |
| Approximate hours | | |
| Signature | | |
