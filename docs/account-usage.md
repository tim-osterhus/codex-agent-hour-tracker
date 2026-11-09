# Private account usage exports

`agent-hours account-usage --label laptop` writes `laptop.json` and `laptop.daily.csv` under `reports/account-usage/`. The long command alias supports the same arguments. Existing files are never overwritten. Success prints a generic message, without identity, token metrics, or dates. Use a new label for each snapshot.

The command uses an existing ChatGPT-backed Codex login. API-key-only login is unsupported. `--codex PATH` selects a native executable; Windows requires `codex.exe`, not an npm shell shim. Only `initialize`, `initialized`, `account/read` with `refreshToken: false`, and `account/usage/read` are sent. No turns, login/logout operations, credential file reads, thread filters, or export uploads occur. Errors omit protocol content, notifications, and app-server stderr.

[Official token-usage documentation](https://learn.chatgpt.com/docs/app-server#7-token-usage-chatgpt) defines nullable summary metrics and optional daily buckets. These service-reported metrics are separate from local runtime and Agent-Hour Score. The service does not document bucket timezone, retention, date filters, pagination, or lifetime completeness. Sparse history is not proof of zero use on missing dates. Null metrics and null buckets remain null in JSON; a null bucket list produces a header-only CSV, so consult JSON to distinguish null from empty history. Dates are preserved verbatim. Duplicate dates remain separate rows; diagnostics flag duplicates and leave the daily total null to avoid implying a reliable total.

JSON uses the private `agent-hours-account-usage` schema, version 1. The account contains a stable SHA-256 fingerprint of the returned email and workspace account ID, or null if neither is available. It enables correlation, not anonymity; it may change when those identity fields change. By default plaintext email, workspace name, account ID, and plan are omitted. `--include-identity` explicitly includes the allowlisted identity fields. Credentials and unknown fields are never exported. Labels are user-supplied private provenance, so avoid sensitive labels when possible.

`--output-dir DIRECTORY` selects another private location. The default `reports/` path is ignored by this repository; it is not automatically ignored in other repositories. Check your own Git exclusions. On POSIX the destination directory is mode 0700 and files are mode 0600; files are created exclusively, without replacement. Parent-directory access, storage encryption, backups, and secure transfer remain your responsibility. A write failure never falls back to stdout.

Direct Windows file output fails closed until private ACLs can be verified. Use explicit `--stdout` only to capture PRIVATE JSON on a secured POSIX host over an authorized SSH connection. For example, after configuring a private capture directory:

```bash
mkdir -p private-capture
chmod 700 private-capture
(umask 077; set -C; ssh WINDOWS_HOST 'agent-hours account-usage --label remote --stdout' > private-capture/remote.json)
```

Check the SSH exit status before using the result; a failed command may leave an empty or incomplete capture. Stdout remains fingerprinted unless `--include-identity` is also supplied. This mode emits JSON only and no success status text. Never paste either JSON or CSV into share cards, public issues, the website, or the community scoreboard. Neither command accepts `--share`. The existing runtime share schema and website payloads are unchanged.

The historical `python scripts/export_account_usage.py` entry point remains a wrapper with the same options, plus the private defaults above.
