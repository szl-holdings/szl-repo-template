# Using this template

1. Create the repository from this template.
2. Replace every placeholder of the form `[[FILL: ...]]` in `README.md` and `CITATION.cff`.
   The check searches `README*`, `CITATION.cff` and `LICENSE*` at the root for the opening `[[FILL`.
3. Keep or adapt the "Scope and data boundary" section. It must keep a non-clinical statement that
   matches the estate DISCLAIM pattern (for example "not a clinical tool" or "non-clinical").
   The word "metaphor" does not count as a data boundary.
   If the repository might touch clinical or patient data, stop and ask the owner first.
4. Keep the Limitations section and give it real content.
5. If you add dependencies, follow `docs/LOCKFILES.md`.
6. Run the check locally: `python -I -B scripts/repo_hygiene_check.py .` (add `--json` for machine output).

As shipped, the template fails the check with exit 1, because the placeholders are still there.
This is intended: a new repository cannot pass until its owner has filled them in. In the template
repository itself (GitHub reports it as a template) the workflow does not run that step. It runs a
template self-check instead, which requires exactly that outcome: exit 1, with `placeholders` as the
only failing guard. The template's own CI therefore fails if its skeleton stops satisfying any other
guard, or if its placeholders are filled in by mistake. Repositories created from the template skip
the self-check and run the real check.

Exit codes: 0 all guards passed; 1 at least one guard failed; 2 internal error or bad usage.
The workflow runs the checker's own self-tests first, so a weakened checker fails CI before it
can approve the repository.
