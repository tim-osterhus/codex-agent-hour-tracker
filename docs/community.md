# Community scoreboard

The community board is optional. You can calculate scores and download cards without signing in or publishing anything.

## What gets posted

The board shows self-reported agent runtime, not verified work or time saved. GitHub sign-in identifies the posting account. It does not prove the score.

Import your public score JSON, review the fields, sign in, and choose **Post my score** after the bot check. Importing alone never publishes.

A post contains your GitHub login, posting time, and these score fields:

- Average, total, and peak daily agent-hours.
- Completed turns and active days.
- Window dates, timezone, and session scope.
- Tracker, methodology, and schema versions.

Human-hours assumptions and Agent Leverage are omitted. Private archives, daily rows, machine names, and session identifiers are rejected.

The board has separate lists for interactive-only and exec-inclusive scores. Each list shows up to 50 recent scores, sorted by hours per day. A new post replaces your current score in that list.

Scores cover 30 completed calendar days. When posted, their end date must be yesterday or one of the seven days before yesterday, in the score's timezone. Old scores leave the recent list. Numeric checks reject malformed data, not dishonest claims.

## Five posts in 30 days

Each GitHub account gets five accepted posts in a rolling 30 × 24-hour window, across both lists. This is not a calendar-month reset.

After the fourth post, the page says you have one left and shows when the next slot opens. After the fifth, you must wait. Each slot opens exactly 30 days after its post was accepted.

Updating a score uses a slot. Failed requests and retries of the same accepted request do not. Deleting a score does not return a slot.

The database enforces the limit in the same transaction that saves the score. Simultaneous requests cannot bypass it.

The service also limits short bursts using a keyed fingerprint of the connecting IP and minute. It does not store raw IP addresses. Shared networks can briefly hit this secondary limit. Multiple GitHub accounts can still evade an account limit. There are no prizes or claims of fraud-proof rankings.

## Privacy and retention

The homepage reads score files locally. Choosing **Share to community** carries an allowlisted score in that browser tab's session storage. Raw imported text is not saved. The community page clears that stored handoff when it loads it. Signing in temporarily saves the same allowlisted draft so you can review it after returning.

The service stores current public scores, GitHub account IDs and logins, login sessions, and posting timestamps. The private ledger includes a request ID and score digest for retry handling. It does not retain prior score JSON.

Login sessions expire after seven days. OAuth sign-in attempts expire after ten minutes. Expired sessions, login attempts, rate buckets, and posting events are pruned in bounded batches during service use. Session cleanup runs during sign-in, not anonymous page reads.

Thirty-day posting records remain after public-score deletion so deletion cannot bypass the limit. Dormant records can remain until another request triggers cleanup. Operators can run cleanup separately.

Current scores remain stored until replaced or deleted, even after they age off the recent board. Account identity records also remain until an operator removes them. Deletion does not erase other people's copies or provider backups. Cloudflare and GitHub receive normal connection metadata. Turnstile contacts Cloudflare during a posting attempt.

Read the [security policy](../SECURITY.md) before sharing. Do not attach raw logs or private archives to an issue.

## Hosting

The existing Cloudflare Pages project serves the site. Pages Functions serve `/api/community/*`. A D1 database stores community data. Turnstile checks posting attempts. No server, tunnel, or connection to the operator's personal machines is required.

This fits Cloudflare's free tiers at small traffic levels, but is not a guaranteed zero-cost service. Functions share the account's Workers allowance. Paid accounts can incur usage charges. Check the account plan and usage before enabling submissions. See [Pages pricing](https://developers.cloudflare.com/pages/functions/pricing/), [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/), and [Turnstile plans](https://developers.cloudflare.com/turnstile/plans/).

Application limits do not stop all abusive traffic from consuming hosting resources. Monitor request volume and D1 usage. Keep submissions disabled if account limits or billing controls are unclear.

### Setup

Use Node.js 24. The Python CLI does not need these development dependencies.

1. Run `npm ci --ignore-scripts`.
2. Run `npm test` and `npm run build:check`.
3. Create a D1 database with `npx wrangler d1 create agent-hours-community`.
4. Set its ID in `wrangler.jsonc` under the `COMMUNITY_DB` binding.
5. Run `npx wrangler d1 migrations apply agent-hours-community --remote`.
6. Register a GitHub OAuth app with the deployed site as its homepage.
7. Set its callback to `https://YOUR_DOMAIN/api/community/auth/callback`, without wildcard matching or device flow.
8. Create a managed Turnstile widget restricted to your deployed hostname, with no pre-clearance.
9. Set the public variables listed below.
10. Store the three secrets through Cloudflare's encrypted Pages secret interface.
11. Deploy with `SUBMISSIONS_ENABLED=false`.
12. Verify response headers, read-only endpoints, and login configuration before enabling posting.

| Variable | Meaning |
| --- | --- |
| `SITE_ORIGIN` | Exact HTTPS origin, without a path or trailing slash |
| `GITHUB_CLIENT_ID` | OAuth application's public client ID |
| `TURNSTILE_SITE_KEY` | Widget's public site key |
| `SUBMISSIONS_ENABLED` | Literal `true` to allow new posts, otherwise disabled |

Store `GITHUB_CLIENT_SECRET`, `SESSION_SECRET`, and `TURNSTILE_SECRET_KEY` as secrets, not public variables. Use a cryptographically random session secret of at least 32 bytes. Never paste secrets into issues, commits, screenshots, or chat.

`npx wrangler pages secret put SECRET_NAME --project-name YOUR_PROJECT` accepts a secret through standard input. Use a password manager or a protected terminal prompt. Do not put secret values in shell arguments or history.

The checked-in preview environment has no production database binding and disables posting. Do not add production credentials to previews or pull-request builds. Use a separate app, widget, and database for authenticated staging tests.

For local work, use `npm run dev`. Database operations are local unless you pass `--remote`. Apply migrations locally with `npx wrangler d1 migrations apply agent-hours-community --local`. API tests use synthetic data and mocked external identity and bot-check responses against a local D1 runtime. They do not contact GitHub or submit public scores.

### Release checks

- Run Python tests, `npm test`, `npm run build:check`, and `npm audit --audit-level=high`.
- Check the homepage, upload, preview, and community handoff on desktop and mobile.
- Confirm the homepage still uses `connect-src 'none'`.
- Confirm only community pages allow the same-origin API and Turnstile.
- Confirm API responses use their own restrictive headers and no wildcard CORS.
- Confirm preview deployments have no production database or secrets.
- Review auth, CSRF, quota concurrency, and deletion tests before enabling posts.
- Never seed the public board with fabricated scores as a smoke test.

The `sharp` development-tool override pins a patched release. It is not part of the production Function bundle. Recheck the override when upgrading Wrangler.

### Stop posting or remove abuse

Set `SUBMISSIONS_ENABLED=false` and redeploy to stop new posts. Check `/api/community/config` returns `enabled: false`. The local score tool remains available.

To remove an abusive score, locate its stable numeric GitHub ID and delete only its rows in `community_scores`. Preserve `quota_events` so removal does not refund slots. Do not delete the database or unrelated account records as a moderation shortcut.

For an auth incident, disable submissions first. Rotate the affected secrets through the hosting provider, revoke affected sessions, and review the OAuth app. Do not copy request bodies, cookies, or credentials into incident logs.

Rollback the Pages deployment if necessary. Do not reverse a database migration without checking data compatibility and backups first.
