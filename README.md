# Odyssey Ticket Monitor — INOX Luxe Phoenix Market City, Velachery

Checks every 15 minutes whether **The Odyssey, 20 July 2026, 7:40 PM** is open for
booking at **INOX Luxe Phoenix Market City, Velachery, Chennai** (PVR INOX theatre 320),
and emails you when the status changes. Runs free on GitHub Actions — your computer
can stay off.

> Why PVR INOX and not district.in? PVR INOX removed their cinemas from district.in,
> so this show will never appear there. The script talks to pvrcinemas.com's own API,
> which is where INOX bookings open first anyway.

## What you get

- **Email when bookings open** (state change), a **success email** when the exact
  7:40 PM show appears, and a **daily heartbeat** email so you know it's alive.
- **Backup alert with zero setup:** on every status change the workflow also opens an
  issue in this repo — GitHub emails you about that automatically, even if you skip
  the SMTP setup below.
- Auto-stops checking after 20 July 2026.

## Deploy (one-time, ~10 minutes)

### 1. Create a private GitHub repo and push this folder

```powershell
cd C:\Users\HP\Desktop\odyssey-monitor
git init -b main
git add .
git commit -m "Odyssey ticket monitor"
gh repo create odyssey-monitor --private --source . --push
```

(Or create the repo on github.com and `git remote add origin ... && git push -u origin main`.)

### 2. Add email secrets (optional but recommended)

Repo page → **Settings → Secrets and variables → Actions → New repository secret**.
Add these five:

| Secret      | Value                                              |
|-------------|----------------------------------------------------|
| `SMTP_HOST` | `smtp.gmail.com` (Gmail/Google Workspace) — see below for others |
| `SMTP_PORT` | `587`                                              |
| `SMTP_USER` | your full email address                            |
| `SMTP_PASS` | an **app password** (NOT your normal password)     |
| `EMAIL_TO`  | where alerts should go, e.g. `lnarayanan@ventureintelligence.info` |

**Getting an app password**
- **Gmail / Google Workspace:** enable 2-Step Verification, then
  [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) →
  create one for "Mail". Use the 16-character code as `SMTP_PASS`.
- **Zoho Mail:** host `smtp.zoho.in`, port `465`. App password from
  Zoho Account → Security → App Passwords.
- **Outlook/Microsoft 365:** host `smtp.office365.com`, port `587`, app password
  from account security settings.

If you skip this step, you still get GitHub's issue-notification emails.

### 3. Test it

Repo page → **Actions** tab → "Check Odyssey tickets" → **Run workflow**.
Open the run's log: you should see a line like

```
Status: date_not_open — Bookings for 2026-07-20 are NOT open yet at this cinema.
```

The first run always sends one email/issue (initial status). After that you only
hear about changes plus one heartbeat per day.

### 4. That's it

The schedule (`*/15 * * * *`) is already in `.github/workflows/check-tickets.yml`.
GitHub Actions cron can run a few minutes late — that's normal.

## When you're done (booked your tickets)

Disable it: repo → Actions → "Check Odyssey tickets" → "…" menu → **Disable workflow**
(or just delete the repo).

## Run locally instead (optional)

```powershell
pip install requests
python check_tickets.py          # loops every 15 min, beeps + opens browser on success
python check_tickets.py --once   # single check
```

## Tweaks (env vars, or edit the defaults in `check_tickets.py`)

| Variable          | Default      | Meaning                                        |
|-------------------|--------------|------------------------------------------------|
| `TARGET_DATE`     | `2026-07-20` | date to watch (yyyy-mm-dd)                      |
| `TARGET_TIME`     | `07:40 PM`   | exact showtime as displayed on pvrcinemas.com   |
| `MOVIE_KEYWORD`   | `ODYSSEY`    | substring matched against the film name         |
| `LANGUAGE_FILTER` | *(empty)*    | `ENGLISH` or `TAMIL` to watch one version only (currently the cinema lists the Tamil version for other dates; empty matches any) |
| `THEATRE_ID`      | `320`        | PVR INOX cinema id (320 = INOX Luxe Phoenix MC Velachery) |
| `HEARTBEAT_HOURS` | `24`         | how often to email even without change          |

## Notes / limitations

- Uses pvrcinemas.com's internal API — if PVR INOX changes it, the check may start
  failing (you'll notice red runs in the Actions tab and missing heartbeats).
- A status-change email is also triggered if the set of Odyssey showtimes changes
  (e.g. new times added), not just the 7:40 PM one.
- GitHub free tier includes far more Actions minutes than this needs (~50 min/day).
