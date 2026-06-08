# Security Policy

## Supported Versions

Only the latest code on the `main` branch is actively supported with security
updates. Older release tags — including `v0.2.0` and any earlier versions — are
**not** supported. If you are running an older version, please upgrade to `main`
or wait for the next tagged release.

| Version | Supported          |
|---------|--------------------|
| `main`  | Yes                |
| `v0.2.0`| No                 |
| `< v0.2.0` | No              |

## Reporting a Vulnerability

If you have found a security issue, please **do not** open a public GitHub
issue. Use one of the private channels below instead.

**Preferred: GitHub private vulnerability reporting**

Open a private security advisory through the repository's Security tab:

> https://github.com/NaustudentX18/vibe-dump/security/advisories/new

This routes the report directly to the maintainers and lets us work on a fix
discretely before any public disclosure.

**Fallback: email**

If the GitHub channel is not an option for you, email
`forest@naustudent.dev`. Encrypt sensitive details at your discretion — a
PGP key is not currently published; if you need one for a coordinated
disclosure, ask and we will provide one.

Please include:

- A clear description of the issue and its impact
- Reproduction steps or a proof-of-concept
- The commit hash, tag, or branch you tested against
- Your name / handle for credit in the advisory (or say "anonymous" if you prefer)

## Response SLA

- **Acknowledgement:** within 72 hours of receipt.
- **Triage and severity assessment:** within 7 days for confirmed issues.
- **Fix release:** depends on severity. Critical issues are patched and
  released as soon as a fix is verified; lower-severity issues may be batched
  into the next regular release.

## What to Expect

After you report a vulnerability, the maintainers will:

1. Acknowledge the report and assign it a tracking reference.
2. Investigate, reproduce, and assess severity. We will keep you informed of
   progress and may reach out for clarification.
3. Develop a fix on a private branch. If you reported the issue and would
   like to be credited, we will coordinate the disclosure wording with you.
4. Release the fix in a tagged version and publish a GitHub Security
   Advisory describing the issue, its impact, and the fix.
5. Coordinate public disclosure timing. We aim to disclose no later than
   90 days after the report, and earlier for critical issues where a fix
   is available.

Thank you for helping keep vibe-dump and its users safe.
