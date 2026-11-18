"""Pure parsers: eClass HTML / iCal text in, plain dicts out. No network here.

eClass is Moodle with the Coursemos theme. All HTML parsers expect the English
UI (the fetcher forces ?lang=en), though most of them key off structure, not text.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

DAYS = {"Mon": 1, "Tue": 2, "Wed": 3, "Thu": 4, "Fri": 5, "Sat": 6, "Sun": 7}

# "Operating System[202602-SOC3010-002]"
COURSE_RE = re.compile(r"^(?P<name>.*?)\s*\[(?P<term>\d{6})-(?P<code>[A-Za-z0-9]+)-(?P<section>\w+)\]\s*$")
# iCal CATEGORIES: "Operating System[2026, FALL SEMESTER, SOC3010, 002, U]" (old courses use round brackets)
CATEGORY_RE = re.compile(
    r"^(?P<name>.*?)\s*[\[(](?P<year>\d{4}),\s*(?P<semester>[^,]+),\s*(?P<code>[A-Za-z0-9]+),\s*(?P<section>\w+),\s*\w+[\])]\s*$"
)
DATETIME_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})\b")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
QUIZ_GRADE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)$")
PERIOD_RE = re.compile(r"^(\d+)(?:~(\d+))?$")
ATTENDANCE_STATES = ("present", "absent", "late", "other")  # column order on the attendance page


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _query_id(url: str) -> int | None:
    value = parse_qs(urlparse(url).query).get("id", [None])[0]
    return int(value) if value and value.isdigit() else None


def _number(text: str | None) -> float | None:
    """'99.00' / '25.00 %' -> float; '-' and empty cells -> None."""
    try:
        return float((text or "").replace("%", "").strip())
    except ValueError:
        return None


def _local_datetime(text: str) -> str | None:
    """'2026-11-20 23:55' -> '2026-11-20T23:55' (naive, in the site's display timezone)."""
    m = DATETIME_RE.search(text or "")
    return f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}" if m else None


def is_logged_in(html: str) -> bool:
    return "login/logout.php" in html


def parse_login_form(html: str, page_url: str) -> tuple[str, dict[str, str]]:
    """Returns (action URL, extra fields) of the username/password form.

    Extra fields are what a browser sends besides the two inputs: hidden inputs
    and the name=value of the submit button.
    """
    for form in _soup(html).find_all("form"):
        if form.find("input", attrs={"name": "password"}):
            fields = {
                i["name"]: i.get("value", "")
                for i in form.find_all("input", attrs={"type": "hidden"})
                if i.get("name")
            }
            submit = form.find("input", attrs={"type": "submit", "name": True})
            if submit:
                fields[submit["name"]] = submit.get("value", "")
            return urljoin(page_url, form.get("action") or page_url), fields
    raise ValueError("login form not found")


def parse_courses(html: str, page_url: str) -> list[dict]:
    """Course list from /local/ubion/user/ (defaults to the current semester)."""
    courses, seen = [], set()
    for a in _soup(html).select("a.coursefullname[href]"):
        url = urljoin(page_url, a["href"])
        cid = _query_id(url)
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        full = _clean(a.get_text())
        m = COURSE_RE.match(full)
        label = a.find_previous_sibling("span")
        cell = a.find_parent("td")
        professor_cell = cell.find_next_sibling("td") if cell else None  # column right after the name
        courses.append({
            "id": cid,
            "name": m["name"] if m else full,
            "code": m["code"] if m else None,
            "section": m["section"] if m else None,
            "term": m["term"] if m else None,
            "mode": _clean(label.get_text()) if label else None,
            "professor": (_clean(professor_cell.get_text()) or None) if professor_cell else None,
            "url": url,
        })
    return courses


def _activity_rows(html: str):
    """Yields (link, texts of the cells after the name cell) for each row of an activity index table."""
    table = _soup(html).select_one("table.generaltable")
    if table is None:  # "There are no ... in this course"
        return
    for tr in table.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        for i, td in enumerate(cells):
            link = td.find("a", href=True)
            if link:
                yield link, [_clean(c.get_text(" ")) for c in cells[i + 1:]]
                break


def parse_assignments(html: str, page_url: str) -> list[dict]:
    """/mod/assign/index.php?id=<course>: Week | Assignments | Due date | Submission | Grade."""
    items = []
    for link, rest in _activity_rows(html):
        url = urljoin(page_url, link["href"])
        submission = rest[1] if len(rest) > 1 else ""
        grade = rest[2] if len(rest) > 2 else ""
        items.append({
            "cmid": _query_id(url),
            "title": _clean(link.get_text()),
            "due": _local_datetime(rest[0]) if rest else None,
            "submitted": submission.lower().startswith("submitted"),
            "graded": grade not in ("", "-"),
            "grade": _number(grade),  # the index page shows no maximum
            "url": url,
        })
    return items


def parse_quizzes(html: str, page_url: str) -> list[dict]:
    """/mod/quiz/index.php?id=<course>: Week | Name | Quiz closes | Grade (close column is optional)."""
    items = []
    for link, rest in _activity_rows(html):
        url = urljoin(page_url, link["href"])
        grade = next((m for m in map(QUIZ_GRADE_RE.match, rest) if m), None)  # "0.60/1.00"
        items.append({
            "cmid": _query_id(url),
            "title": _clean(link.get_text()),
            "due": next((d for d in map(_local_datetime, rest) if d), None),
            "graded": grade is not None,
            "grade": float(grade[1]) if grade else None,
            "max": float(grade[2]) if grade else None,
            "url": url,
        })
    return items


def parse_grade_report(html: str, page_url: str) -> list[dict] | None:
    """/grade/report/user/index.php?id=<course> -> flat list of grade entries.

    Returns None when the teacher has hidden the report ("Can not view grades.").
    kind: module name ('assign', 'quiz', ...), 'manual', 'calc', or 'category' /
    'total' for the "<category> SUM" and "Course total" rows. Rows are told
    apart by structure (classes, icons, links), not by their English labels.
    """
    table = _soup(html).select_one("table.user-grade")
    if table is None:
        return None
    entries, categories = [], {}  # categories: nesting level -> name of the open category
    for tr in table.find_all("tr"):
        th = tr.find("th", class_="column-itemname")
        classes = th.get("class", []) if th else []
        level = next((int(c[5:]) for c in classes if re.fullmatch(r"level\d+", c)), None)
        if level is None:  # table header
            continue
        grade_cell = tr.find("td", class_="column-grade")
        if grade_cell is None:  # category heading: no value cells
            categories = {k: v for k, v in categories.items() if k < level}
            categories[level] = _clean(th.get_text())
            continue
        header = th.select_one(".gradeitemheader") or th
        link = th.find("a", href=True)
        icon = (th.find("img") or {}).get("src", "")
        range_cell = tr.find("td", class_="column-range")
        entry = {
            "name": _clean(header.get_text()),
            "kind": "manual",
            "category": categories.get(level - 1) if level > 2 else None,
            "cmid": None,
            "grade": _number(grade_cell.get_text()),
            "max": _number(re.split(r"[–-]", range_cell.get_text())[-1]) if range_cell else None,
        }
        if "baggt" in classes:  # aggregate row of the category / the whole course
            entry["kind"] = "total" if level == 1 else "category"
            entry["name"] = "Course total" if level == 1 else categories.get(level, entry["name"])
            entry["category"] = None
        elif link:
            url = urljoin(page_url, link["href"])
            module = re.search(r"/mod/(\w+)/", url)
            entry["kind"] = module[1] if module else "manual"
            entry["cmid"] = _query_id(url)
        elif "/i/calc" in icon:
            entry["kind"] = "calc"
        entries.append(entry)
    return entries


def parse_attendance(html: str) -> dict | None:
    """/local/ubattendance/my_status.php?id=<course>: Week | Class | Attendance | Absence | Late | Others.

    Returns counts per state plus the sessions that were not attended:
    {present, absent, late, other, missed: [{date, status}]}; None without the table.
    """
    table = _soup(html).select_one("table.attendance_my")
    if table is None:
        return None
    result = {state: 0 for state in ATTENDANCE_STATES}
    result["missed"] = []
    for tr in table.find_all("tr"):
        cells = [_clean(td.get_text()) for td in tr.find_all("td", recursive=False)]
        if len(cells) < 6 or not DATE_RE.match(cells[0]):  # "There is no week found."
            continue
        state = next((s for s, mark in zip(ATTENDANCE_STATES, cells[2:6]) if mark), None)
        if state is None:  # session exists but is not marked yet
            continue
        result[state] += 1
        if state != "present":
            result["missed"].append({"date": cells[0], "status": state})
    return result


def parse_schedule(html: str) -> list[dict]:
    """Syllabus page, 'Schedule' row: '<td>B-209,B-210 Mon<br>Wed 4~6<br>3~5</td>'.

    The cell is three smashed columns: rooms (comma-separated), days and period
    ranges (one per class meeting). Returns [{day: 1..7, from, to, room}].
    """
    th = _soup(html).find("th", string=re.compile(r"^\s*Schedule\s*$"))
    td = th.find_next_sibling("td") if th else None
    if td is None:
        return []
    tokens = td.get_text(" ").split()
    first_day = next((i for i, t in enumerate(tokens) if t in DAYS), None)
    if first_day is None:
        return []
    rooms = [r.strip() for r in " ".join(tokens[:first_day]).split(",") if r.strip()]
    days = [DAYS[t] for t in tokens[first_day:] if t in DAYS]
    periods = [PERIOD_RE.match(t) for t in tokens[first_day:] if t not in DAYS]
    periods = [m for m in periods if m]
    slots = []
    for i, (day, m) in enumerate(zip(days, periods)):
        if len(rooms) == len(days):
            room = rooms[i]
        else:
            room = ", ".join(rooms) or None
        slots.append({"day": day, "from": int(m[1]), "to": int(m[2] or m[1]), "room": room})
    return slots


def parse_ical_url(html: str, page_url: str) -> str | None:
    """The tokenised 'iCal' quick-export link on the calendar page."""
    a = _soup(html).find("a", href=re.compile(r"calendar/export_execute\.php"))
    return urljoin(page_url, a["href"]) if a else None


def _ics_unescape(value: str) -> str:
    return re.sub(r"\\([,;\\nN])", lambda m: "\n" if m[1] in "nN" else m[1], value)


def _ics_time(params: str, value: str):
    """Returns ('YYYY-MM-DD', True) for all-day values, (aware UTC datetime, False) otherwise."""
    if "T" not in value:
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}", True
    dt = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    return dt, False


def parse_ics(text: str) -> list[dict]:
    """Moodle calendar export -> [{uid, summary, category, start, end, all_day}].

    Note: Moodle exports assignment deadlines as all-day events whose date is
    taken in UTC, so the time of day is lost (and very early local times shift
    a day back). Timed events (quizzes with an open/close window) keep both ends.
    """
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold continuation lines
    events, cur = [], None
    for line in text.splitlines():
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur and "DTSTART" in cur:
                start, all_day = _ics_time(*cur["DTSTART"])
                end = _ics_time(*cur["DTEND"])[0] if "DTEND" in cur else start
                events.append({
                    "uid": cur.get("UID", ("", ""))[1],
                    "summary": _clean(_ics_unescape(cur.get("SUMMARY", ("", ""))[1])),
                    "category": _clean(_ics_unescape(cur.get("CATEGORIES", ("", ""))[1])) or None,
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                })
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, _, params = head.partition(";")
            cur[name.upper()] = (params.upper(), value)
    return events


def parse_category(category: str | None) -> dict | None:
    m = CATEGORY_RE.match(category or "")
    if not m:
        return None
    return {
        "name": m["name"],
        "year": int(m["year"]),
        "semester": m["semester"].strip(),
        "code": m["code"],
        "section": m["section"],
    }
