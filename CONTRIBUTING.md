# Contributing to vibe-dump

Welcome. vibe-dump is a small, opinionated project for turning spoken thoughts into structured blueprints, and we are glad you are here. Whether you are filing a bug, opening a pull request, sketching an idea in an issue, or just poking around the source, you are a contributor. This guide is short on purpose — read it once and you are good to go.

## How to contribute

- **Bug reports** — Open a GitHub issue. Include the smallest reproduction you can, your Python/OS versions, and the relevant log output. If a bug only reproduces with a specific voice sample, attach or describe it.
- **Pull requests** — Fork, branch, push, open a PR against `main`. Small, focused PRs land faster than sweeping rewrites. If you are planning a large change, open an issue first so we can talk through the design before you sink time into it.
- **Ideas and questions** — GitHub Discussions is the right place. Issues are for things that need to be done; discussions are for things that need to be thought about.
- **Security issues** — Do not open a public issue. See [`SECURITY.md`](./SECURITY.md).

## Development setup

```bash
git clone https://github.com/NaustudentX18/vibe-dump.git
cd vibe-dump
pip install -e ".[web,all]"
python -m pytest
```

The `[web,all]` extra pulls in the FastAPI dashboard, the LLM provider adapters, and the hardware bridges used in tests. Use a virtualenv — this project has opinions about its dependency graph and a global Python install will eventually disagree with you.

## Testing

```bash
python -m pytest -q
```

Target **80% coverage** for new code. The CI pipeline runs the full suite plus coverage on every PR; a drop below threshold will fail the build. If you are adding a new module, write the tests first — the existing tests are a good map of what each public function is expected to do.

## Code style

- **Formatter / linter:** `ruff` is configured in `pyproject.toml`. Run `ruff check .` and `ruff format .` before pushing. CI will reject diffs that ruff complains about.
- **Immutability preferred.** Prefer building new objects over mutating existing ones. If a function takes a dict and returns a slightly different dict, return a copy.
- **Type hints everywhere** on new code. The project targets Python 3.11+.
- **Small functions, small files.** If a function is past 50 lines or a file is past 800, it probably wants to be split.
- **Explicit error handling.** No silent `except: pass`. If you must catch a broad exception, log it and re-raise or convert to a domain error.

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/). Format:

```
<type>(<scope>): <short description>

<optional body explaining the why>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`. Scopes are short and lowercase — `voice`, `blueprint`, `swarm`, `web`, `hardware`, `db`, etc. The subject line is imperative mood, no trailing period, under 72 characters.

## Pull request process

1. Open the PR against `main`.
2. Make sure CI is green — tests, lint, type check, coverage.
3. Request a review from a maintainer. PRs land with **one approval** from a code owner; you do not need two.
4. Squash-merge by default. Keep the main history clean; the per-commit history lives on your branch.
5. If your PR closes an issue, reference it with `Closes #123` in the body.

## Code of conduct and security

- Everyone who interacts with this project is expected to follow the [Code of Conduct](./CODE_OF_CONDUCT.md).
- Security issues are handled separately and confidentially — see [SECURITY.md](./SECURITY.md) for the reporting process and response SLA.
