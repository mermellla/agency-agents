# Prompts (owner-versioned, §7.7)

One directory per role (`decision`, `triage`, `critique`, `review`), one file per version `vX.Y.Z.md` with YAML front
matter (`version`, `date`, `changelog`). The worker computes `prompt_version` as the highest version present across
roles and stores each file's hash in `prompt_versions`; a reused version string with different content is refused
(ADR-0019). The agent never edits these files (§2). Bumping a version opens a new experiment phase (§13.2).

`decision/v0.1.0.md` is the Slice 1 skeleton of the stable, cacheable prefix; Slice 5 delivers v1.0.0.
