# CLAUDE.md

## Vercel dashboard deployments require Hobby-plan-compliant cron schedules

`dashboard/vercel.json` defines a Vercel Cron job (`/api/health-check`).
**Vercel's free Hobby plan only allows cron schedules that run once per day.**
A more frequent schedule (e.g. `*/15 * * * *`) fails `vercel.json` validation
on every single deployment attempt — and it fails **silently**: no deployment
row appears in the dashboard, no error is shown anywhere in the Vercel UI, a
manually-triggered Deploy Hook returns `201 Created` with a job that never
progresses past `PENDING`, and even a full GitHub App uninstall/reinstall
does not fix it. The only way this surfaced was via `vercel --prod` (Vercel
CLI), which prints the actual validation error directly in the terminal:

```
Error: Hobby accounts are limited to daily cron jobs. This cron expression
(*/15 * * * *) would run more than once per day. Upgrade to the Pro plan to
unlock all Cron Jobs features on Vercel.
```

This was the root cause of an ~2-month stretch (roughly Sep 3 through Oct 20,
2026) where the dashboard appeared completely stuck on an old commit despite
many merged PRs. Before finding this, extensive (ultimately unnecessary)
troubleshooting was done: reconnecting the Vercel account's Git login
connection, fully uninstalling/reinstalling the Vercel GitHub App, creating a
brand-new Vercel project from scratch, disconnecting/reconnecting the
project's Git integration multiple times, and fixing an unrelated real bug
(GitHub repo's default branch was stuck on an old feature branch instead of
`main`) — none of which fixed it, because none of those were the actual
cause.

**If the dashboard ever appears stuck again (commits merged to `main` but no
new Vercel deployment shows up):**
1. Check `dashboard/vercel.json` first for any cron schedule stricter than
   once/day — that alone can silently block every deployment.
2. If unsure, run `vercel --prod` from the `dashboard` folder locally — the
   CLI will surface build/validation errors that the GitHub-triggered
   pipeline swallows silently.
3. Only fall back to the Git-connection/GitHub-App troubleshooting path
   (account reconnect, App reinstall, etc.) if `vercel --prod` also succeeds
   with no errors but GitHub pushes still don't trigger anything.
