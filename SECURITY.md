# Security Policy

## Supported versions

Security fixes target the latest released `maida-ai` package. Upgrade older versions. Reports about older versions are welcome; reproduce on the latest release when possible. Include the installed package version and, for an unreleased checkout, the commit SHA.

## Reporting a vulnerability

Email [security@maida.ai](mailto:security@maida.ai) privately. Do not post vulnerability details publicly before coordinated disclosure.

Include the package version, Python version, operating system, impact, reproduction steps and a contact address. Identify any adapter or importer involved. Remove secrets, customer data and sensitive traces from the reproduction. Use simulated data where possible; do not send credentials or unredacted trace files.

## Response and disclosure

We aim to acknowledge reports within five business days. This is an acknowledgement target, not a guaranteed fix deadline. Resolution depends on severity and complexity. We coordinate investigation, fixes and disclosure with the reporter, and communicate the next steps after initial triage.

## Repository security controls

- [Dependabot](.github/dependabot.yml) checks the root uv project, standalone Python examples and GitHub Actions weekly. Enable and review repository alerts and security updates separately.
- [CodeQL](.github/workflows/codeql.yml) scans Python, JavaScript/TypeScript and Actions on PRs/pushes to `main` and `release/**`, weekly and on dispatch. Use advanced setup without also enabling default setup.
- Verify successful scans and triage alerts; configuration alone is not evidence of a clean scan. Branch review requirements, secret scanning and push protection are repository or organization settings that must be verified separately.

## Local data

Runs, baselines and policies can contain sensitive data even when payload redaction is enabled. Keep secrets out of checked-in artifacts, restrict access to local storage and CI runners, and review artifact uploads before sharing them. Report redaction or data-exposure problems with a simulated or redacted example.

If a report involves the GitHub Action, include the `maida-assert` SHA/version and the selected engine version as well. See the [Action security policy](https://github.com/maida-ai/maida-assert/blob/main/SECURITY.md) for runner-specific reporting guidance.
