# ADR-0019 — Version computation and phase-opening mechanics

**Appendix C item:** — (implied by §7.7, §10.5, §13.2) · **Status:** Accepted · **Date:** 2026-09-13

## Context
§13.2: any change to the prompt, risk config, scanner weights or version, regime thresholds, QB rules, model IDs,
exclusion list, or a bug fix creates a new phase with a recorded reason and the full set of versions in force; nothing
changes silently or online. The spec names the version keys but not how they are computed or how a phase is opened.

## Decision
| Version | Computed as | Registry |
|---|---|---|
| `config_version` | sha256 of the canonical JSON of `config/risk_policy.yaml` + `config/fees.yaml` | `config_versions` (content stored) |
| `scanner_version` | explicit semver string in `config/scanner.yaml`; the registry stores the file content and **refuses** a reused version string with different content | `scanner_versions` |
| `qb_rules_version` | explicit semver in `config/qb_rules.yaml`, same rule | `qb_rules_versions` |
| `exclusion_list_version` | `<exclusions.yaml version>+sic:<sic_backstop.yaml version>`, content hash stored, same rule | `exclusion_list_versions` |
| `prompt_version` | semver in the prompt file's front matter (`prompts/<role>/vX.Y.Z.md`), hash stored, same rule | `prompt_versions` |
| `migration_version` | filename of the newest applied migration | `experiment_phases` |
| `code_version` | git SHA of the deployed worker (`RAILWAY_GIT_COMMIT_SHA`) | `experiment_phases` |
| model IDs | from `risk_policy.yaml` (already inside `config_version`), also stored explicitly on the phase | `experiment_phases` |

Boot sequence (before reconciliation): compute the versions; load the open phase; if every version and the code SHA
match, continue. If anything differs, read `config/phase_change.yaml`: it must carry a non-empty `reason`, a `category`
(`strategy` | `correctness` | `safety`), and `for_versions` naming the new values that changed. If it does, close the
open phase (`ended_at = now()`), insert the next phase with all versions, log `what_changed` (old → new per key, plus the
code SHA), email `phase_opened`. If it does not, **halt** with `PHASE_REASON_MISSING`. The database refuses a decision
whose versions differ from its phase (`VERSION_DRIFT`, tested), so a missed reason cannot leak into the dataset.

Schema migrations bump `migration_version` and therefore open a phase (§10.5); by convention they are accompanied by a
`config_version` change only when a config key changes.

## Alternatives considered
- Opening phases manually via SQL: relies on process, not schema.
- Hash-only versions for everything: unreadable in reports; explicit semver plus content check keeps both.

## Consequences
- Every deploy that changes behaviour is visible as a phase; the report shows closed trades per phase (D-58).
- Shadow variants remain the way to test a change without spending primary trades (§13.2).
