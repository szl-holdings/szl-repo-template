# [[FILL: repository name]]

## Scope and data boundary

This repository is not a clinical tool. It must never receive patient data, PHI
or clinical records. [[FILL: name the data it does use, for example "synthetic
data only" or "public literature only"]]

## Purpose

[[FILL: one or two sentences on what this repository does, for whom, and what
question it answers]]

## One-command reproduction

```bash
[[FILL: one command that reproduces the headline result from a clean checkout]]
```

[[FILL: what the command prints or writes, how long it takes, and which
Python or tool versions it was last run with]]

## Limitations

- [[FILL: what this repository does not establish]]
- [[FILL: conditions that were not tested, and known failure modes]]

## Evidence labels

Every claim in this README, and in reports produced from this repository, carries one label:

- **MEASURED**: observed in a run that anyone can repeat with the command above.
- **REPORTED**: stated by a cited document or another source, and not re-run here.
- **MODELED**: derived from measurements or from reading the code, not observed directly.
- **UNKNOWN**: not established.
- **CONJECTURE**: a hypothesis, stated as one.

## Repository hygiene

`scripts/repo_hygiene_check.py` runs on every push and pull request
(`.github/workflows/repo-hygiene.yml`). It fails closed: it exits 0 only when
CITATION.cff, the Limitations section, this data-boundary section, the
LICENSE and the dependency locks are all present, and every template
placeholder has been filled. See `docs/TEMPLATE.md` and `docs/LOCKFILES.md`.
