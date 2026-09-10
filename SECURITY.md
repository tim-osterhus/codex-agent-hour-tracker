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

Public share exports use a separate allowlist of aggregate fields. The website validates that schema, rejects private archives, and renders images locally. It has no analytics, cookies, or data-submission endpoint. Downloaded cards still disclose their displayed aggregates and optional human-hours assumptions.

All imported data is untrusted. A share card is self-reported and provides no cryptographic proof of runtime, identity, or productivity.

## Reproduction details

Describe the affected version, the command or input shape involved, and the observed impact. Provide a minimal reproduction that contains synthetic data. Do not include private session content.
