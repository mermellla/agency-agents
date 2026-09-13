# Versioned configuration

Every file in this directory is versioned content that the system records on every decision
(§7.6, §10.2). Nothing here is edited by the agent at runtime (§2 "No self-modification").

| File | Version key recorded | Governs |
|---|---|---|
| `risk_policy.yaml` | `config_version` (sha256 of the canonical YAML, see ADR-0019) | Appendix B keys: capital, limits, horizon, staleness, budget, guards, feed plan |
| `exclusions.yaml` | `exclusion_list_version` | Ethical denylist (§4.4 item 1). **Seed only — owner review required before PAPER.** |
| `sic_backstop.yaml` | part of `exclusion_list_version` | SIC-code deny and default-deny lists (§4.4 items 2–3), verified against EDGAR on 2026-09-13 (ADR-0006) |
| `fees.yaml` | part of `config_version` | Regulatory fee schedule (§9, ADR-0012) |
| `scanner.yaml` | `scanner_version` | Composite-score weights and thresholds (ADR-0008), regime thresholds (ADR-0009) |
| `qb_rules.yaml` | `qb_rules_version` | Quant baseline QB-1.0 rules (§12) |
| `phase_change.yaml` | — | Reason and category the owner supplies when any of the above changes (ADR-0019) |

Secrets never live here. They are Railway environment variables only (§11).
