# Experimental Short-Term Trading Agent — documentation map

| Directory | Contents |
|---|---|
| `spec/` | The authoritative specification (v2.3) and its reading rules |
| `phase0/` | The Phase 0 engineering package: index, traceability matrix, open issues, architecture, interfaces, schema, test plan, implementation sequence, API verification log |
| `adr/` | Architecture Decision Records for every Appendix C item plus two implied ones |

Code and data in this repository that belong to the same package: `config/` (versioned configuration seeds),
`supabase/migrations/` (repo-tracked schema), `src/tradeagent/` (typed models, interfaces, config loader),
`tests/` (pre-implementation tests). Start at `phase0/00-phase0-index.md`.
