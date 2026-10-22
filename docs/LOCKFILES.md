# Lockfile convention

A dependency manifest must not float. `scripts/repo_hygiene_check.py` (guard `lockfile`)
applies these rules to every manifest in the tree, except inside `.git`, `node_modules`,
`.venv`, `venv` and `__pycache__`.

| Manifest | Passes when |
|---|---|
| `pyproject.toml` | no dependencies declared; or `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `pylock.toml`, `pylock.*.toml`, `requirements.lock` or `conda-lock.yml` in its directory or an ancestor; or every dependency pinned with `==`; or a requirements file in the same directory pins every declared dependency with `==` |
| `requirements*.txt` | every requirement is pinned with `==` or `===` (hashes, markers and `-r` includes allowed); or a Python lockfile as above |
| `package.json` | no `dependencies`, `devDependencies` or `optionalDependencies`; or `package-lock.json`, `npm-shrinkwrap.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lock` or `bun.lockb` in its directory or an ancestor; or every version exact (`1.2.3`) |
| `Cargo.toml` | no dependencies; or `Cargo.lock` in its directory or an ancestor (workspace root) |
| `go.mod` | no `require` directive; or `go.sum` in the same directory |

Rules of thumb:

1. Standard-library-only Python needs no lockfile. Leave `dependencies` empty or omit the manifest.
2. Commit the lockfile your tool writes (`uv lock`, `poetry lock`, `npm install`, `cargo generate-lockfile`, `go mod tidy`).
3. Ranges in a manifest are fine when a lockfile is committed next to it or above it.
4. Dynamic dependencies (`dynamic = ["dependencies"]`) need a lockfile or a fully pinned requirements file.
5. `[build-system] requires` and `peerDependencies` are not checked.
6. An unparsable manifest fails. A missing `-r` include fails.

The check reads only manifest and lockfile names and contents. It never installs or runs anything.
