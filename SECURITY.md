# Security

## Supported versions

The latest version is the supported version. Update to the latest published version before investigating or reporting a security issue. Older versions may not contain current fixes.

## Private reports

Report vulnerabilities privately through [GitHub Security Advisories](https://github.com/tim-osterhus/codex-agent-hour-tracker/security/advisories) and GitHub's private vulnerability reporting workflow. Do not use a public issue for undisclosed vulnerabilities.

Never attach Codex session files, transcripts, or full generated reports to a public issue. Those files can contain sensitive activity data even when the report looks operational.

The `--share` command emits a bounded aggregate designed for sharing. Sharing its dates, counts, or durations is still an intentional disclosure. Review the card before publishing it.

## Data boundaries

The scanner reads local archive bytes and decodes only bounded timing and source metadata. It does not decode conversation, reasoning, or tool payloads. The CLI does not send session data over the network.

Private merge exports retain exact start timestamps, durations, source classes, and persistent hashed turn identifiers. Hashes permit correlation and do not anonymize activity. Keep these exports private. Full text and CSV reports also reveal activity patterns.

Public share exports use a separate allowlist of aggregate fields. The website validates that schema, rejects private archives, and renders images locally. Importing a score does not submit it. Downloaded cards still disclose their displayed aggregates and optional human-hours assumptions.

## Optional community scoreboard

Publishing requires a separate confirmation on the community page. A post links your GitHub login to aggregate runtime, dates, timezone, scope, and software versions. It excludes daily rows, machine labels, turn identifiers, and human-hours assumptions. Public scores are visible to anyone and can be copied.

Choosing **Share to community** temporarily puts an allowlisted score in browser tab storage for the page handoff. It does not store the raw file or private archive. This is separate from posting to the server.

GitHub login requests no repository or email scopes. The service uses a Secure, HttpOnly, SameSite login cookie and checks the request origin and CSRF token for changes. GitHub access tokens are discarded after identity lookup. The site has no analytics scripts. Cloudflare Turnstile loads when you choose to post, and contacts Cloudflare for a bot check.

The database stores a stable GitHub account ID, login, session records, current public scores, and a short-lived posting ledger. Five accepted posts are allowed per account in a rolling 30-day window. A separate short-burst limit uses time-bucketed keyed IP fingerprints, not raw IP addresses. Fingerprints are private and are not a guarantee of anonymity.

Deleting a score removes its current public entry. It does not refund posting slots or erase copies others made. Expired private records are pruned during service use. Provider backups and operational logs can have separate retention. Hosting providers still receive normal connection metadata, including IP addresses. See the [community guide](docs/community.md) for retention and operator controls.

All imported data is untrusted. A share card is self-reported and provides no cryptographic proof of runtime, identity, or productivity.

## Reproduction details

Describe the affected version, the command or input shape involved, and the observed impact. Provide a minimal reproduction that contains synthetic data. Do not include private session content.
