# Security policy

## Supported versions

askdocs is pre-1.0. Fixes land on `main` and ship in the next release; older
tags are not back-patched.

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub's private vulnerability reporting](https://github.com/Simon-oid/askdocs/security/advisories/new)
(Security tab → "Report a vulnerability"). Do not open a public issue for
an exploitable bug. You should get an acknowledgement within a few days; a
minimal reproducer is the fastest to act on.

## Scope

askdocs has no authentication of its own: deploy it behind a proxy that has. Prompt injection from the indexed documents is reduced, not solved: the measurements and the defences are in `KNOWN_LIMITATIONS.md` and `evals/results/injection.md`.
