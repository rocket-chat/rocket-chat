# Contributing to Rocket Chat

Thank you for helping build Rocket Chat. This page covers licensing and where to start; the working rules live in [`AGENTS.md`](AGENTS.md).

## Licensing

Rocket Chat is free and open-source software under the **[GNU General Public License v3.0](LICENSE)**.

- **Share alike:** any distributed modification, fork or derivative work must be published under GPL-3.0.
- **Attribution:** keep the original copyright notices and a clear reference to the upstream repository.
- **No proprietary enclosures:** modified binaries must come with the corresponding source.

By contributing you agree that your contributions are licensed under GPL-3.0.

## Where to start

1. Read [`AGENTS.md`](AGENTS.md): setup and commands, the gates every change must pass, engineering and testing rules, documentation policy, git conventions.
2. Read [`dev/architecture.md`](dev/architecture.md) and [`dev/security-and-tenancy.md`](dev/security-and-tenancy.md) before changing structure, data flow or anything touching identity, tenants or secrets.
3. Branch from `main`, make small Conventional Commits, update `CHANGELOG.md` under `## [Unreleased]`, and open a pull request. It merges when every CI gate is green.

## Reporting problems

- **Bugs:** open a GitHub issue with logs, reproduction steps and your environment (Docker or Kubernetes).
- **Security vulnerabilities:** do not open a public issue; email `contact@nathp.fr`.
