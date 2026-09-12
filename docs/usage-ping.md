# Optional usage counts

Collection is **off by default**. Maida configures no collector URL, enables no
receiver, and makes no usage request unless `MAIDA_USAGE_OPT_IN=1` is explicitly
set. Local tracing and the demo remain offline. The prepared receiver is separate
from the local trace viewer; it has not been deployed by this change.

Completed `maida assert` and `maida run` commands print the opt-in state to stderr,
leaving JSON/Markdown stdout and gate exit codes unchanged. Setup failures do not
send a ping. A failed delivery never changes the gate verdict. Requests have a
one-second socket timeout, no retries, no redirects, and no environment proxies;
DNS resolution can take longer. Do not enable this path where that delay is
unacceptable. `demo`, `drift`, and library calls do not send usage counts.

## Payload and consent

The published [JSON schema](../schemas/usage-ping.v1.schema.json) allows only:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `version` | Installed Maida version |
| `repo_id` | Operator-provided random 64-character lowercase hex identifier |
| `date` | Execution date in UTC, `YYYY-MM-DD` |
| `pr` | Whether `GITHUB_EVENT_NAME` is `pull_request` or `pull_request_target` |
| `verdict_counts` | `pass`, `fail`, `inconclusive`: one is 1, the others 0 |

No repository name, remote, path, commit, PR number, trace, prompt, tool payload,
environment contents, or user identity is included. Maida never derives an ID
from a remote URL. Use a random value, not an unsalted hash of a discoverable
repository name. The stable identifier is pseudonymous, **not anonymous**: it
allows consenting-repository activity to be linked across dates. Keep its private
mapping outside the repository. A receiver necessarily sees the connection IP;
the application neither records nor uses it for analytics.

To opt in later, the operator must explicitly set all three environment variables:
`MAIDA_USAGE_OPT_IN=1`, `MAIDA_USAGE_ENDPOINT` to an approved HTTPS URL without
credentials/query/fragment, and `MAIDA_USAGE_REPO_ID` to the random identifier.
Generate an identifier locally with `uv run python -c 'import secrets; print(secrets.token_hex(32))'`.
Keep the same identifier across consenting runs. Unset `MAIDA_USAGE_OPT_IN` to stop
sending immediately; this does not delete previously received records. No flags
or collector settings are added to generated workflows or policy files.

## Prepared append-only receiver

`maida.usage.create_receiver(Path("usage.jsonl"))` returns a disabled ASGI app
(POST returns 503). A future operator can explicitly pass `enabled=True` to
accept validated POSTs at `/usage`. Each accepted event appends one JSON line to
the chosen local file, created with mode 0600. Bodies larger than 2048 bytes,
extra fields, and invalid values are rejected. There are no read, delete, or
update endpoints, and the receiver stores no headers or client IPs.

Before enabling a deployment, its operator must choose the endpoint and data
retention/access policy, provide TLS and abuse controls, disable server access
logs (`uvicorn --no-access-log`) and proxy/IP logging, and verify those settings.
The local file is append-only through this API, not tamper-proof storage; an
operator still controls file access and disposal. No deployment is configured.

## What these counts establish

Unique IDs observed in a week measure consenting active repositories, not all
installs. Counts represent completed invocations, so reruns can add counts.
Lost requests are not evidence of inactivity. These unauthenticated observations
are not audited installation or usage totals. PR context is self-reported by the
runner environment and is not independently verified.

For [retention measurement](../scripts/retention.md), retain the initial install
timestamp privately and corroborate PR activity. A date-only observation crossing
an install-relative week boundary needs a voluntary check-in or more precise
private evidence. Missing pings remain unknown. Download counts cannot fill that
gap. Neither this implementation nor its tests establishes actual retention.
