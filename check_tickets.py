"""
Monitor PVR INOX for The Odyssey tickets at INOX Luxe Phoenix Market City,
Velachery, Chennai (theatreId 320) on 24 July 2026 — 7:40 PM show.

Talks directly to the same API the pvrcinemas.com website uses:
  POST https://api3.pvrcinemas.com/api/v1/booking/content/csessions
(The "Authorization: Bearer " header must be present but may be empty,
 and "cid" must be sent as a number — the API rejects it as a string.)

Runs in two modes:
  python check_tickets.py            -> loop forever, check every 15 minutes (local use)
  python check_tickets.py --once     -> single check, then exit (GitHub Actions / cron)

Sends an email when the status CHANGES (bookings open / target show found),
plus a daily heartbeat so you know the monitor is alive. If SMTP is not
configured it just prints — and in GitHub Actions a repo issue is created
instead (see .github/workflows/check-tickets.yml), which GitHub emails you.

Only dependency: requests
"""

import json
import os
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import requests

IST = timezone(timedelta(hours=5, minutes=30))

# Windows consoles often use cp1252, which cannot print emoji in log lines.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ------------------------- Configuration (env overridable) -------------------------
THEATRE_ID = int(os.environ.get("THEATRE_ID", "320"))   # INOX Luxe Phoenix MC, Velachery
CITY = os.environ.get("CITY", "Chennai")
TARGET_DATE = os.environ.get("TARGET_DATE", "2026-07-24")     # yyyy-mm-dd
TARGET_TIME = os.environ.get("TARGET_TIME", "07:40 PM")       # as shown on pvrcinemas.com
MOVIE_KEYWORD = os.environ.get("MOVIE_KEYWORD", "ODYSSEY")
# Optional: "ENGLISH" or "TAMIL" to match only that version; empty = any language.
LANGUAGE_FILTER = os.environ.get("LANGUAGE_FILTER", "").strip().upper()

CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "900"))
HEARTBEAT_HOURS = float(os.environ.get("HEARTBEAT_HOURS", "24"))

STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
ALERT_FILE = Path(os.environ.get("ALERT_FILE", "alert_message.txt"))

# SMTP settings — all via environment (GitHub secrets in the cloud).
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
EMAIL_TO = os.environ.get("EMAIL_TO", SMTP_USER)
# ------------------------------------------------------------------------------------

API_URL = "https://api3.pvrcinemas.com/api/v1/booking/content/csessions"
API_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Content-Type": "application/json",
    "Authorization": "Bearer ",     # required, empty is fine for public data
    "chain": "INOX",
    "city": CITY,
    "appVersion": "1.0",
    "platform": "WEBSITE",
    "country": "INDIA",
    "Origin": "https://www.pvrcinemas.com",
    "Referer": "https://www.pvrcinemas.com/",
}


def log(msg):
    print(f"[{datetime.now(IST):%Y-%m-%d %H:%M:%S} IST] {msg}", flush=True)


def fetch_sessions(session, dated):
    body = {"city": CITY, "cid": THEATRE_ID, "lat": "0.00", "lng": "0.00",
            "dated": dated, "qr": "NO", "cineType": "", "cineTypeQR": ""}
    resp = session.post(API_URL, json=body, headers=API_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def film_language(film_name):
    """'THE ODYSSEY (TAMIL) (T.B.A)' -> 'TAMIL'"""
    if "(" in film_name:
        return film_name.split("(", 1)[1].split(")", 1)[0].strip().upper()
    return ""


def extract_odyssey_shows(output):
    """Return [(film_name, [showtimes...])] for films matching MOVIE_KEYWORD."""
    results = []
    for entry in output.get("cinemaMovieSessions") or []:
        film = (entry.get("movieRe") or {}).get("filmName") or ""
        if MOVIE_KEYWORD.upper() not in film.upper():
            continue
        if LANGUAGE_FILTER and not film_language(film).startswith(LANGUAGE_FILTER):
            continue
        times = set()
        for exp in entry.get("experienceSessions") or []:
            for show in exp.get("shows") or []:
                t = (show.get("showTime") or "").strip().upper()
                if t:
                    times.add(t)

        def clock_order(t):
            try:
                return datetime.strptime(t, "%I:%M %p")
            except ValueError:
                return datetime.max
        results.append((film, sorted(times, key=clock_order)))
    return results


def check_status(session):
    """One API check. Returns a status dict, or None on transient API failure."""
    data = fetch_sessions(session, TARGET_DATE)

    if data.get("result") != "success":
        # Either the date isn't open for booking yet, or the API hiccuped.
        # Ask for the currently open dates to tell the difference.
        try:
            na = fetch_sessions(session, "NA")
        except requests.RequestException:
            na = None
        if not na or na.get("result") != "success":
            log(f"API error for both '{TARGET_DATE}' and 'NA' — treating as transient.")
            return None
        days = [d.get("dt") for d in (na.get("output") or {}).get("days") or []]
        if TARGET_DATE in days:
            # Date is listed but the dated query failed — transient, retry next cycle.
            log("Date is listed but session query failed — treating as transient.")
            return None
        return {
            "state": "date_not_open",
            "summary": f"Bookings for {TARGET_DATE} are NOT open yet at this cinema.",
            "detail": f"Dates currently open for booking: {', '.join(days) or 'none'}",
        }

    output = data.get("output") or {}
    shows = extract_odyssey_shows(output)

    if not shows:
        open_movies = sorted({(e.get("movieRe") or {}).get("filmName") or "?"
                              for e in output.get("cinemaMovieSessions") or []})
        return {
            "state": "date_open_no_odyssey",
            "summary": (f"Bookings for {TARGET_DATE} are OPEN at this cinema, "
                        f"but no '{MOVIE_KEYWORD}' shows are listed yet."),
            "detail": "Movies currently listed: " + (", ".join(open_movies) or "none"),
        }

    lines = [f"  {film}: {', '.join(times)}" for film, times in shows]
    for film, times in shows:
        if TARGET_TIME.upper() in times:
            return {
                "state": "target_found",
                "summary": (f"🎉 {film} at {TARGET_TIME} on {TARGET_DATE} is OPEN "
                            f"for booking — go book now!"),
                "detail": "All Odyssey showtimes found:\n" + "\n".join(lines),
            }
    return {
        "state": "odyssey_open_no_target",
        "summary": (f"Odyssey bookings for {TARGET_DATE} are OPEN at this cinema, "
                    f"but no {TARGET_TIME} show (yet)."),
        "detail": "Showtimes found:\n" + "\n".join(lines),
    }


def signature(status):
    return json.dumps([status["state"], status["detail"]], sort_keys=True)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def send_email(subject, body):
    if not (SMTP_USER and SMTP_PASS and EMAIL_TO):
        log("SMTP not configured (SMTP_USER/SMTP_PASS/EMAIL_TO) — skipping email.")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO
    msg.set_content(body)
    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30,
                                  context=ssl.create_default_context()) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        log(f"Email sent to {EMAIL_TO}: {subject}")
        return True
    except Exception as e:
        log(f"Email sending failed: {e}")
        return False


def email_body(status):
    return (
        f"{status['summary']}\n\n"
        f"{status['detail']}\n\n"
        f"Cinema : INOX Luxe Phoenix Market City, Velachery, Chennai (theatre {THEATRE_ID})\n"
        f"Target : {MOVIE_KEYWORD} on {TARGET_DATE} at {TARGET_TIME}"
        + (f" ({LANGUAGE_FILTER})" if LANGUAGE_FILTER else "") + "\n"
        f"Checked: {datetime.now(IST):%Y-%m-%d %H:%M:%S} IST\n\n"
        f"Book at: https://www.pvrcinemas.com/ (Chennai > INOX Luxe Phoenix Market City)\n"
    )


def run_check(session, loop_mode):
    """Returns True when the target show has been found (monitor can stop)."""
    now = datetime.now(IST)
    if now.date() > datetime.strptime(TARGET_DATE, "%Y-%m-%d").date():
        log(f"Target date {TARGET_DATE} has passed — nothing to monitor. "
            f"Disable the workflow / stop the script.")
        return True

    log("Checking INOX Luxe Phoenix Market City, Velachery...")
    try:
        status = check_status(session)
    except requests.RequestException as e:
        log(f"Network/API error: {e} — will retry next cycle.")
        return False
    if status is None:
        return False

    log(f"Status: {status['state']} — {status['summary']}")
    log(status["detail"])

    state = load_state()
    sig = signature(status)
    changed = state.get("signature") != sig
    last_email = state.get("last_email_utc", 0)
    heartbeat_due = (time.time() - last_email) > HEARTBEAT_HOURS * 3600

    if changed or heartbeat_due:
        prefix = "🎬 TICKETS" if status["state"] == "target_found" else \
                 ("🎬 Update" if changed else "🎬 Heartbeat")
        subject = f"{prefix}: {status['summary'][:120]}"
        send_email(subject, email_body(status))
        state["last_email_utc"] = time.time()

    if changed:
        # Hand the alert to the GitHub Actions workflow (creates a repo issue).
        ALERT_FILE.write_text(status["summary"] + "\n" + email_body(status),
                              encoding="utf-8")
        state["signature"] = sig
        state["state"] = status["state"]
        state["updated_ist"] = f"{now:%Y-%m-%d %H:%M:%S}"

    save_state(state)

    if status["state"] == "target_found":
        if loop_mode:
            try:
                import winsound
                for _ in range(5):
                    winsound.Beep(1200, 400)
                    time.sleep(0.1)
            except Exception:
                print("\a" * 5)
            import webbrowser
            webbrowser.open("https://www.pvrcinemas.com/")
        return True
    return False


def main():
    once = "--once" in sys.argv
    print(f"Monitoring '{MOVIE_KEYWORD}'"
          + (f" ({LANGUAGE_FILTER})" if LANGUAGE_FILTER else "")
          + f" on {TARGET_DATE} at {TARGET_TIME} — theatre {THEATRE_ID}, {CITY}.")
    session = requests.Session()

    if once:
        run_check(session, loop_mode=False)
        return

    print(f"Checking every {CHECK_INTERVAL_SECONDS // 60} minutes. Press Ctrl+C to stop.\n")
    while True:
        if run_check(session, loop_mode=True):
            log("Monitoring finished.")
            break
        log(f"Next check in {CHECK_INTERVAL_SECONDS // 60} minutes...\n")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
