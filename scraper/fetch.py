"""Collects homework, the class schedule, grades and attendance from eClass into site/data.json.

Mode is picked from the environment:
  ECLASS_USERNAME + ECLASS_PASSWORD  full scrape: submission status, exact deadlines,
                                     schedule, grades and attendance
  ECLASS_ICAL_URL                    calendar-only fallback: no password, but no status,
                                     no time of day for assignments, no schedule, no grades
Run it in a terminal with none of them set to be prompted for the login.

--public (used by the workflow) prepares the file for the public site: with
DATA_PASSPHRASE set everything is encrypted, without it grades and attendance
are left out.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

import envelope
import parsers

BASE = os.environ.get("ECLASS_BASE", "https://eclass.inha.ac.kr").rstrip("/")
TZ = timezone(timedelta(hours=5))  # Asia/Tashkent, no DST; eClass shows deadlines in it
TZ_NAME = "Asia/Tashkent"
FIRST_PERIOD_START = 8 * 60  # period 1 starts at 08:00 ...
PERIOD_MINUTES = 30          # ... and every period lasts 30 minutes
REQUEST_DELAY = 0.3          # seconds between requests, to stay polite
OUT_DEFAULT = Path(__file__).resolve().parent.parent / "site" / "data.json"
MIN_PASSPHRASE = 12  # the encrypted file is public: a short passphrase can be brute-forced offline

ATTENDANCE_RE = re.compile(r"\[\w+\]-\d+교시$")  # auto-created attendance events
QUIZ_SUFFIX_RE = re.compile(r"\s*\(Quiz (opens|closes)\)$", re.I)
SEMESTER_ORDER = {"SPRING": 1, "SUMMER": 2, "FALL": 3, "WINTER": 4}


def log(message: str) -> None:
    print(message, file=sys.stderr)


def read_password(prompt: str) -> str:
    """getpass.getpass, except that on Windows the line is read the way input() reads it.

    getpass reads a Windows console key press by key press and silently drops
    characters the console delivers as Alt+Numpad sequences, which happens to
    characters missing from the console's keyboard layout ('&' with a Russian
    layout). Line input with the echo switched off gets them like input() does.
    """
    if os.name != "nt":
        return getpass.getpass(prompt)
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
    mode = wintypes.DWORD()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):  # not a console after all
        return getpass.getpass(prompt)
    enable_echo_input = 0x0004
    print(prompt, end="", file=sys.stderr, flush=True)
    kernel32.SetConsoleMode(handle, mode.value & ~enable_echo_input)
    try:
        line = sys.stdin.readline()
    finally:
        kernel32.SetConsoleMode(handle, mode.value)
        print(file=sys.stderr)  # the Enter key was not echoed either
    if not line:
        raise EOFError
    return line.rstrip("\r\n")


LOGIN_ERRORS = {  # Moodle's login/index.php sends failures back with ?errorcode=N
    "1": "eClass did not get its session cookie back (errorcode 1): a problem in this script, not in the password",
    "3": "eClass rejected the ID or password (errorcode 3): check the keyboard layout and Caps Lock",
    "4": "eClass reports a timed-out session (errorcode 4): run the script again",
}


def describe_hop(url: str) -> str:
    """Path and query keys of a URL; values are hidden except Moodle's errorcode."""
    parts = urlparse(url)
    query = "&".join(f"{key}={values[0] if key == 'errorcode' else '...'}"
                     for key, values in parse_qs(parts.query).items())
    return parts.path + (f"?{query}" if query else "")


def login_problem(chain: list[str]) -> str:
    """Explains a failed login from the URLs the form POST went through, the last being where it ended."""
    route = " -> ".join(map(describe_hop, chain))
    codes = [parse_qs(urlparse(url).query).get("errorcode", [None])[0] for url in chain]
    code = next((c for c in reversed(codes) if c), None)
    if code:
        reason = LOGIN_ERRORS.get(code, f"eClass refused the login (errorcode {code})")
    elif any("testsession" in parse_qs(urlparse(url).query) for url in chain):
        reason = "eClass accepted the password, but the session did not stick: a problem in this script"
    else:
        reason = "eClass login failed for an unknown reason"
    return f"{reason}\n  route of the login form: {route}"


class Client:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (eclass-board; personal homework dashboard)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def _send(self, method: str, url: str, allow: tuple = (), **kwargs) -> requests.Response:
        # Errors mention only the path: the iCal URL carries a token in its query
        # string and CI logs of a public repository are public.
        path = urlparse(url).path
        try:
            response = self.session.request(method, url, timeout=30, **kwargs)
        except requests.RequestException as error:
            raise RuntimeError(f"{type(error).__name__} for {path}") from None
        if response.status_code >= 400 and response.status_code not in allow:
            raise RuntimeError(f"HTTP {response.status_code} for {path}")
        time.sleep(REQUEST_DELAY)
        return response

    def get(self, url: str) -> str:
        return self._send("GET", url).text

    def page(self, url: str, allow: tuple = ()) -> str:
        """GET a page that requires the session; `allow` lists error statuses to accept."""
        html = self._send("GET", url, allow=allow).text
        if not parsers.is_logged_in(html):
            raise RuntimeError(f"session lost at {urlparse(url).path}")
        return html

    def login(self, username: str, password: str) -> None:
        login_url = f"{BASE}/login.php"
        action, fields = parsers.parse_login_form(self.get(login_url), login_url)
        # Sent the way a browser sends the form: with its submit button and the page it came from.
        response = self._send("POST", action, data={**fields, "username": username, "password": password},
                              headers={"Referer": login_url, "Origin": BASE})
        chain = [hop.url for hop in response.history] + [response.url]
        # ?lang=en pins the UI language for this session only; parsers expect English.
        if not parsers.is_logged_in(self.get(f"{BASE}/local/ubion/user/?lang=en")):
            raise SystemExit(login_problem(chain))


def local_iso(naive: str | None) -> str | None:
    """'2026-11-20T23:55' as shown by eClass -> ISO 8601 with the Tashkent offset."""
    if not naive:
        return None
    return datetime.fromisoformat(naive).replace(tzinfo=TZ).isoformat(timespec="seconds")


def period_times(first: int, last: int) -> dict:
    def fmt(minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    return {
        "start": fmt(FIRST_PERIOD_START + (first - 1) * PERIOD_MINUTES),
        "end": fmt(FIRST_PERIOD_START + last * PERIOD_MINUTES),
    }


def norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", QUIZ_SUFFIX_RE.sub("", title)).strip().casefold()


def grade_group(name: str, kind: str, category: str | None = None) -> str:
    """Which block of the statistics tab an activity or grade item belongs to.

    Teachers name things freely (a midterm mark kept as an assignment, a quiz
    kept as a manual item), so the name wins over the module type, and the
    gradebook category is only the last resort.
    """
    text = name.lower()
    if re.search(r"mid[\s-]*term", text):
        return "midterm"
    if re.search(r"\bfinal\b", text) and not re.search(r"project|report|presentation", text):
        return "final"
    if "attendance" in text:
        return "attendance"
    if re.search(r"quiz|mcq", text) or kind == "quiz":
        return "quiz"
    if kind == "assign":
        return "homework"
    category = (category or "").lower()
    for prefix, group in (("midterm", "midterm"), ("final", "final"), ("attendance", "attendance"),
                          ("assign", "homework"), ("quiz", "quiz")):
        if category.startswith(prefix):
            return group
    return "other"


def assign_task(course_id, item: dict) -> dict:
    status = "graded" if item["graded"] else "submitted" if item["submitted"] else "pending"
    return {
        "id": f"assign-{item['cmid']}", "type": "assign", "group": grade_group(item["title"], "assign"),
        "courseId": course_id, "title": item["title"], "due": local_iso(item["due"]), "allDay": False,
        "status": status, "url": item["url"],
    }


def quiz_task(course_id, item: dict) -> dict:
    return {
        "id": f"quiz-{item['cmid']}", "type": "quiz", "group": grade_group(item["title"], "quiz"),
        "courseId": course_id, "title": item["title"], "due": local_iso(item["due"]), "allDay": False,
        "status": "graded" if item["graded"] else "pending", "url": item["url"],
    }


def course_stats(course_id, assignments: list[dict], quizzes: list[dict],
                 report: list[dict] | None, attendance: dict | None) -> dict:
    """Grades and attendance of one course for the statistics tab.

    The gradebook report is the main source. Teachers can hide it, so grades
    shown on the assignment / quiz index pages fill in whatever it lacks.
    taskId links a grade to its entry in "tasks".
    """
    grades, seen = [], set()
    for entry in report or []:
        aggregate = entry["kind"] in ("category", "total")
        if aggregate and (entry["grade"] is None or not entry["max"]):
            continue  # an empty sum row says nothing about the student
        task_id = f"{entry['kind']}-{entry['cmid']}" if entry["cmid"] and entry["kind"] in ("assign", "quiz") else None
        seen.add(task_id)
        grades.append({
            "name": entry["name"], "kind": entry["kind"],
            "group": "total" if entry["kind"] == "total"
            else grade_group(entry["name"], entry["kind"], entry["name"] if aggregate else entry["category"]),
            "taskId": task_id, "grade": entry["grade"], "max": entry["max"],
        })
    for kind, items in (("assign", assignments), ("quiz", quizzes)):
        for item in items:
            task_id = f"{kind}-{item['cmid']}"
            if item["grade"] is not None and task_id not in seen:
                grades.append({
                    "name": item["title"], "kind": kind, "group": grade_group(item["title"], kind),
                    "taskId": task_id, "grade": item["grade"], "max": item.get("max"),
                })
    return {"courseId": course_id, "gradesHidden": report is None, "grades": grades, "attendance": attendance}


def event_task(course_id, event: dict, url: str | None) -> dict:
    if event["all_day"]:
        due = event["start"]
    else:  # a window (e.g. quiz open..close): the deadline is its end
        due = event["end"].astimezone(TZ).isoformat(timespec="seconds")
    return {
        "id": f"event-{event['uid'].split('@')[0]}",
        "type": "quiz" if QUIZ_SUFFIX_RE.search(event["summary"]) else "event",
        "courseId": course_id,
        "title": QUIZ_SUFFIX_RE.sub("", event["summary"]),
        "due": due, "allDay": event["all_day"],
        "status": "unknown", "url": url,
    }


def calendar_tasks(events: list[dict], resolve, known_titles: dict) -> list[dict]:
    """Calendar events that are deadlines and are not already listed as assignments/quizzes.

    resolve(event) -> (course_id, url), or None to drop the event.
    """
    tasks = []
    for event in events:
        summary = event["summary"]
        if ATTENDANCE_RE.search(summary) or summary.lower().endswith("(quiz opens)"):
            continue
        resolved = resolve(event)
        if resolved is None:
            continue
        course_id, url = resolved
        if norm_title(summary) in known_titles.get(course_id, ()):
            continue
        tasks.append(event_task(course_id, event, url))
    return tasks


def course_resolver(courses: list[dict], calendar_url: str):
    """Maps a calendar event to one of the current courses by its CATEGORIES line."""
    by_code = {(c["code"], c["section"]): c for c in courses}

    def resolve(event: dict):
        category = parsers.parse_category(event["category"])
        if category is None:  # personal / site-wide event, or an unknown category format
            return None, calendar_url
        course = by_code.get((category["code"], category["section"]))
        return (course["id"], course["url"]) if course else None  # None: a course of another semester

    return resolve


def payload(source: str, courses=(), tasks=(), schedule=(), stats=(), warnings=()) -> dict:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "baseUrl": BASE,
        "timezone": TZ_NAME,
        "courses": list(courses),
        "tasks": sorted(tasks, key=lambda t: (t["due"] is None, t["due"] or "", t["title"])),
        "schedule": sorted(schedule, key=lambda s: (s["day"], s["from"])),
        "stats": list(stats),
        "warnings": list(warnings),
    }


def for_publishing(data: dict, passphrase: str) -> dict:
    """What may go to the public site: everything encrypted, or everything but grades and attendance."""
    if not passphrase:
        return {**data, "stats": [], "privateOmitted": True}
    if len(passphrase) < MIN_PASSPHRASE:
        raise SystemExit(
            f"DATA_PASSPHRASE is shorter than {MIN_PASSPHRASE} characters; generate a random one: "
            'python -c "import secrets; print(secrets.token_urlsafe(18))"'
        )
    return envelope.seal(data, passphrase)


def collect_with_login(username: str, password: str) -> dict:
    client = Client()
    client.login(username, password)

    courses_url = f"{BASE}/local/ubion/user/"
    courses = parsers.parse_courses(client.page(courses_url), courses_url)
    if not courses:
        log("warning: no courses in the current semester")

    tasks, schedule, stats, warnings, known_titles = [], [], [], [], {}
    for course in courses:
        cid = course["id"]
        label = course["code"] or cid
        url = f"{BASE}/mod/assign/index.php?id={cid}"
        assignments = parsers.parse_assignments(client.page(url), url)
        url = f"{BASE}/mod/quiz/index.php?id={cid}"
        quizzes = parsers.parse_quizzes(client.page(url), url)
        tasks += [assign_task(cid, a) for a in assignments]
        tasks += [quiz_task(cid, q) for q in quizzes]
        known_titles[cid] = {norm_title(i["title"]) for i in assignments + quizzes}
        try:
            slots = parsers.parse_schedule(client.page(f"{BASE}/local/ubion/setting/syllabus.php?id={cid}"))
        except RuntimeError as error:
            warnings.append(f"schedule of {label} skipped: {error}")
            slots = []
        schedule += [{"courseId": cid, **s, **period_times(s["from"], s["to"])} for s in slots]

        report = attendance = None
        try:
            # A report hidden by the teacher answers 404 with "Can not view grades."
            url = f"{BASE}/grade/report/user/index.php?id={cid}"
            report = parsers.parse_grade_report(client.page(url, allow=(404,)), url)
        except RuntimeError as error:
            warnings.append(f"grade report of {label} skipped: {error}")
        try:
            attendance = parsers.parse_attendance(client.page(f"{BASE}/local/ubattendance/my_status.php?id={cid}"))
        except RuntimeError as error:
            warnings.append(f"attendance of {label} skipped: {error}")
        stats.append(course_stats(cid, assignments, quizzes, report, attendance))

    # Calendar adds what the two index pages do not cover: polls, manual events.
    calendar_url = f"{BASE}/calendar/view.php?view=upcoming"
    try:
        ical_url = parsers.parse_ical_url(client.page(calendar_url), calendar_url)
        if ical_url is None:
            raise RuntimeError("iCal link not found on the calendar page")
        events = parsers.parse_ics(client.get(ical_url))
        tasks += calendar_tasks(events, course_resolver(courses, calendar_url), known_titles)
    except RuntimeError as error:
        warnings.append(f"calendar events skipped: {error}")

    for warning in warnings:
        log(f"warning: {warning}")
    return payload("login", courses, tasks, schedule, stats, warnings)


def collect_from_ical(ical_url: str) -> dict:
    text = Client().get(ical_url)
    if "BEGIN:VCALENDAR" not in text:
        raise SystemExit("ECLASS_ICAL_URL did not return a calendar (the token changes with the password)")
    events = parsers.parse_ics(text)

    def semester_rank(category: dict) -> tuple:
        return category["year"], SEMESTER_ORDER.get(category["semester"].split()[0].upper(), 0)

    categories = {e["category"]: parsers.parse_category(e["category"]) for e in events if e["category"]}
    parsed = [c for c in categories.values() if c]
    latest = max(map(semester_rank, parsed), default=None)
    courses = {}
    for category in parsed:  # only the latest semester: old courses keep stale events
        if semester_rank(category) == latest:
            key = f"{category['code']}-{category['section']}"
            courses[key] = {
                "id": key, "name": category["name"], "code": category["code"],
                "section": category["section"], "term": None, "mode": None, "professor": None, "url": None,
            }
    calendar_url = f"{BASE}/calendar/view.php?view=upcoming"

    def resolve(event: dict):
        category = categories.get(event["category"])
        if category is None:
            return None, calendar_url
        key = f"{category['code']}-{category['section']}"
        return (key, calendar_url) if semester_rank(category) == latest and key in courses else None

    tasks = calendar_tasks(events, resolve, {})
    return payload("ical", courses.values(), tasks)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT, help="where to write data.json")
    ap.add_argument("--public", action="store_true", help="prepare the file for the public site (see above)")
    args = ap.parse_args()

    username = os.environ.get("ECLASS_USERNAME", "").strip()
    password = os.environ.get("ECLASS_PASSWORD", "")
    ical_url = os.environ.get("ECLASS_ICAL_URL", "").strip()
    interactive = not (username and password) and not ical_url and sys.stdin.isatty()
    if interactive:
        try:
            username = input("eClass ID: ").strip()
            password = read_password("eClass password (not echoed, not stored): ")
        except EOFError:  # Windows reports NUL as a tty; treat as non-interactive
            username = password = ""

    if username and password:
        if interactive:  # the terminal shows nothing while the password is typed; this is the check
            log(f"logging in as {username!r}, password received: {len(password)} characters")
        data = collect_with_login(username, password)
    elif ical_url:
        data = collect_from_ical(ical_url)
    else:
        log("no credentials configured: writing a setup placeholder")
        data = payload("none")
        data["setupRequired"] = True  # the page shows setup instructions instead of data

    summary = (f"{len(data['courses'])} courses, {len(data['tasks'])} tasks, {len(data['schedule'])} "
               f"schedule slots, stats of {len(data['stats'])} courses ({data['source']} mode)")
    if args.public:
        data = for_publishing(data, os.environ.get("DATA_PASSPHRASE", ""))
        summary += ", encrypted" if data.get("encrypted") else ", grades and attendance left out (no DATA_PASSPHRASE)"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    indent = None if data.get("encrypted") else 1
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    log(f"wrote {args.out}: {summary}")


if __name__ == "__main__":
    main()
