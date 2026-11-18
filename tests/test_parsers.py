"""Parser tests on fragments captured from eclass.inha.ac.kr (personal values replaced).

Run: python -m unittest discover -s tests
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))

import fetch  # noqa: E402
import parsers  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://eclass.inha.ac.kr"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# Real export lines: CRLF endings, folded with a leading tab (sometimes mid-escape,
# sometimes followed by an empty continuation line).
ICS = "\r\n".join([
    "BEGIN:VCALENDAR", "METHOD:PUBLISH", "PRODID:-//John Papaioannou/NONSGML Bennu 0.1//EN", "VERSION:2.0",
    "BEGIN:VEVENT", "UID:65971@eclass.inha.ac.kr", "SUMMARY:Engineering Communications[001]-1교시",
    "DESCRIPTION:", "CLASS:PUBLIC", "LAST-MODIFIED:20261112T025655Z", "DTSTAMP:20261116T185119Z",
    "DTSTART;VALUE=DATE:20261111", "DTEND;VALUE=DATE:20261112",
    "CATEGORIES:Engineering Communications[2026\\, FALL SEMESTER\\, NTS3020\\, 001\\", "\t, U]", "\t",
    "END:VEVENT",
    "BEGIN:VEVENT", "UID:65780@eclass.inha.ac.kr", "SUMMARY:Assignment#3",
    "DESCRIPTION:Submission Deadline: 13 /Nov/ 2026 (23:30 Hr)", "CLASS:PUBLIC",
    "DTSTART;VALUE=DATE:20261113", "DTEND;VALUE=DATE:20261114",
    "CATEGORIES:Computer Algorithm[2026\\, FALL SEMESTER\\, SOC3030\\, 002\\, U]",
    "END:VEVENT",
    "BEGIN:VEVENT", "UID:63124@eclass.inha.ac.kr", "SUMMARY:MCQS-9", "DESCRIPTION:", "CLASS:PUBLIC",
    "DTSTART:20261116T060000Z", "DTEND:20261120T185900Z",
    "CATEGORIES:The New History of Uzbekistan\\, Philosophy\\, Basics of Spiritual",
    "\tity and Jurisprudence-2[2026\\, FALL SEMESTER\\, NTS3032\\, 002\\, U]", "\t",
    "END:VEVENT",
    "BEGIN:VEVENT", "UID:66040@eclass.inha.ac.kr", "SUMMARY:Choice Midterm Paper Review Sessions closes",
    "DESCRIPTION:Dear Students\\,To ensure everyone receives focused attention an",
    "\td to avoid overcrowding\\, I’ve created a poll.Room:&nbsp\\;A406", "\t", "CLASS:PUBLIC",
    "DTSTART;VALUE=DATE:20261202", "DTEND;VALUE=DATE:20261203",
    "CATEGORIES:Operating System[2026\\, FALL SEMESTER\\, SOC3010\\, 002\\, U]",
    "END:VEVENT",
    "BEGIN:VEVENT", "UID:55855@eclass.inha.ac.kr", "SUMMARY:Project Submission", "DESCRIPTION:", "CLASS:PUBLIC",
    "DTSTART;VALUE=DATE:20261212", "DTEND;VALUE=DATE:20261213",
    "CATEGORIES:Application Programming in Java(2025\\, FALL SEMESTER\\, MSC2070\\,", "\t 003\\, U)", "\t",
    "END:VEVENT",
    "BEGIN:VEVENT", "UID:65286@eclass.inha.ac.kr",
    "SUMMARY:[QUIZ 4]  FOUR ROOMS — completion code 2% (Quiz closes)", "DESCRIPTION:NO DEADLINE", "CLASS:PUBLIC",
    "DTSTART;VALUE=DATE:20261231", "DTEND;VALUE=DATE:20270101",
    "CATEGORIES:Operating System[2026\\, FALL SEMESTER\\, SOC3010\\, 002\\, U]",
    "END:VEVENT",
    "END:VCALENDAR", "",
])


class HtmlParsers(unittest.TestCase):
    def test_courses(self):
        url = f"{BASE}/local/ubion/user/"
        courses = parsers.parse_courses(fixture("my_courses.html"), url)
        self.assertEqual([c["id"] for c in courses], [2581, 2584])
        self.assertEqual(courses[0], {
            "id": 2581, "name": "Engineering Communications", "code": "NTS3020", "section": "001",
            "term": "202602", "mode": "OFFLINE", "professor": "Professor A",
            "url": f"{BASE}/course/view.php?id=2581",
        })
        # commas and a "-2" inside the name must not confuse the [term-code-section] split
        self.assertEqual(courses[1]["name"],
                         "The New History of Uzbekistan, Philosophy, Basics of Spirituality and Jurisprudence-2")
        self.assertEqual((courses[1]["code"], courses[1]["section"], courses[1]["mode"]),
                         ("NTS3032", "002", "ONLINE"))

    def test_assignments(self):
        url = f"{BASE}/mod/assign/index.php?id=2608"
        items = parsers.parse_assignments(fixture("assign_index.html"), url)
        self.assertEqual([i["cmid"] for i in items], [73336, 69767, 71247, 72901])  # divider rows skipped
        midterm, hw1, hw2, hw3 = items
        self.assertEqual((midterm["due"], midterm["submitted"], midterm["graded"]), (None, False, True))
        self.assertEqual((hw1["due"], hw1["submitted"], hw1["graded"]), ("2026-09-20T23:55", True, False))
        self.assertEqual((hw2["due"], hw2["submitted"], hw2["graded"]), (None, False, False))
        self.assertEqual((hw3["due"], hw3["submitted"], hw3["graded"]), ("2026-11-15T23:55", False, False))
        self.assertEqual(hw1["title"], "Homework 1 — Count-Min Sketch")
        self.assertEqual(hw3["url"], f"{BASE}/mod/assign/view.php?id=72901")
        self.assertEqual((midterm["grade"], hw1["grade"]), (30.0, None))

    def test_quizzes(self):
        url = f"{BASE}/mod/quiz/index.php?id=2604"
        items = parsers.parse_quizzes(fixture("quiz_index.html"), url)
        self.assertEqual([i["cmid"] for i in items], [70126, 70773, 70780, 72487])
        self.assertEqual((items[0]["due"], items[0]["graded"]), ("2026-09-18T09:00", True))
        self.assertEqual((items[1]["due"], items[1]["graded"]), ("2026-11-20T23:59", False))
        self.assertEqual((items[2]["due"], items[2]["graded"]), (None, False))
        self.assertEqual(items[1]["url"], f"{BASE}/mod/quiz/view.php?id=70773")  # relative href resolved
        self.assertEqual((items[0]["grade"], items[0]["max"]), (90.0, 100.0))
        self.assertEqual((items[1]["grade"], items[1]["max"]), (None, None))

    def test_grade_report(self):
        url = f"{BASE}/grade/report/user/index.php?id=2604"
        entries = parsers.parse_grade_report(fixture("grade_report.html"), url)
        self.assertEqual([(e["name"], e["kind"], e["category"], e["cmid"], e["grade"], e["max"]) for e in entries], [
            ("Midterm exam", "category", None, None, 20.0, 40.0),
            ("Offline Attendance", "manual", "Attendance", None, 20.0, 26.0),
            ("Attendance (10%)", "calc", "Attendance", None, 7.69, 10.0),
            ("Attendance", "category", None, None, None, 36.0),
            ("Etc.", "category", None, None, None, 0.0),
            ("[SOC3010-002] Quiz 1 (1%)", "quiz", None, 70126, 90.0, 100.0),
            ("[SOC3010-002] Homework Assignment 3 (2.5%)", "assign", None, 73195, None, 100.0),
            ("Midterm Manual", "manual", None, None, 12.0, 25.0),
            ("Course total", "total", None, None, None, 100.0),
        ])
        # a report hidden by the teacher
        self.assertIsNone(parsers.parse_grade_report("<p>Can not view grades.</p>", url))

    def test_attendance(self):
        self.assertEqual(parsers.parse_attendance(fixture("attendance.html")), {
            "present": 2, "absent": 1, "late": 1, "other": 0,  # the unmarked fifth session is not counted
            "missed": [{"date": "2026-09-09", "status": "absent"}, {"date": "2026-09-16", "status": "late"}],
        })
        empty = ('<table class="attendance_my"><tbody><tr><td colspan="6" class="text-center">'
                 'There is no week found.</td></tr></tbody></table>')
        self.assertEqual(parsers.parse_attendance(empty),
                         {"present": 0, "absent": 0, "late": 0, "other": 0, "missed": []})
        self.assertIsNone(parsers.parse_attendance("<p>no table</p>"))

    def test_no_activities(self):
        html = "<html><body><p>There are no Assignments in this course</p></body></html>"
        self.assertEqual(parsers.parse_assignments(html, BASE), [])
        self.assertEqual(parsers.parse_quizzes(html, BASE), [])

    def test_schedule(self):
        def row(cell):
            return f'<table><tr><th class="text-center">Schedule</th><td>{cell}</td><th>Grade</th><td>3.0</td></tr></table>'

        self.assertEqual(parsers.parse_schedule(row("B-209,B-210 Mon<br>Wed 4~6<br>3~5")), [
            {"day": 1, "from": 4, "to": 6, "room": "B-209"},
            {"day": 3, "from": 3, "to": 5, "room": "B-210"},
        ])
        self.assertEqual(parsers.parse_schedule(row("B-101 Tue<br>Thu 1~3<br>1~3")), [
            {"day": 2, "from": 1, "to": 3, "room": "B-101"},
            {"day": 4, "from": 1, "to": 3, "room": "B-101"},
        ])
        self.assertEqual(parsers.parse_schedule(row("")), [])
        self.assertEqual(parsers.parse_schedule("<p>online course, no schedule row</p>"), [])

    def test_ical_url(self):
        html = ('<div class="bottom"><a href="https://eclass.inha.ac.kr/calendar/export_execute.php?preset_what=all'
                '&amp;preset_time=recentupcoming&amp;userid=0&amp;authtoken=0000" class="ical-link m-l-1">iCal</a></div>')
        self.assertEqual(
            parsers.parse_ical_url(html, f"{BASE}/calendar/view.php"),
            f"{BASE}/calendar/export_execute.php?preset_what=all&preset_time=recentupcoming&userid=0&authtoken=0000",
        )
        self.assertIsNone(parsers.parse_ical_url("<p>nothing</p>", BASE))

    def test_login_form(self):
        html = ('<form action="https://eclass.inha.ac.kr/login/index.php" method="post">'
                '<input type="hidden" name="logintoken" value="abc"><input type="text" name="username">'
                '<input type="password" name="password"><input type="submit" name="loginbutton"></form>')
        self.assertEqual(parsers.parse_login_form(html, f"{BASE}/login.php"),
                         (f"{BASE}/login/index.php", {"logintoken": "abc", "loginbutton": ""}))

    def test_login_problem(self):
        wrong = fetch.login_problem([f"{BASE}/login/index.php", f"{BASE}/login.php?errorcode=3"])
        self.assertIn("errorcode 3", wrong)
        self.assertIn("/login/index.php -> /login.php?errorcode=3", wrong)
        lost = fetch.login_problem([f"{BASE}/login/index.php", f"{BASE}/login/index.php?testsession=3472", f"{BASE}/"])
        self.assertIn("did not stick", lost)
        self.assertNotIn("3472", lost)  # the internal user id stays out of logs
        self.assertIn("unknown reason", fetch.login_problem([f"{BASE}/login/index.php"]))


class Calendar(unittest.TestCase):
    def setUp(self):
        self.events = parsers.parse_ics(ICS)

    def test_parse_ics(self):
        self.assertEqual(len(self.events), 6)
        attendance, assignment, quiz = self.events[:3]
        self.assertEqual(attendance["category"], "Engineering Communications[2026, FALL SEMESTER, NTS3020, 001, U]")
        self.assertEqual((assignment["start"], assignment["all_day"]), ("2026-11-13", True))
        self.assertFalse(quiz["all_day"])
        self.assertEqual(quiz["end"], datetime(2026, 11, 20, 18, 59, tzinfo=timezone.utc))
        self.assertEqual(
            quiz["category"],
            "The New History of Uzbekistan, Philosophy, Basics of Spirituality and Jurisprudence-2"
            "[2026, FALL SEMESTER, NTS3032, 002, U]",
        )

    def test_parse_category(self):
        self.assertEqual(parsers.parse_category(self.events[2]["category"]), {
            "name": "The New History of Uzbekistan, Philosophy, Basics of Spirituality and Jurisprudence-2",
            "year": 2026, "semester": "FALL SEMESTER", "code": "NTS3032", "section": "002",
        })
        self.assertEqual(parsers.parse_category(self.events[4]["category"])["year"], 2025)  # round brackets
        self.assertIsNone(parsers.parse_category(None))
        self.assertIsNone(parsers.parse_category("Some site event"))

    def test_login_mode_adds_only_new_deadlines(self):
        courses = [
            {"id": 2581, "code": "NTS3020", "section": "001", "url": "u2581"},
            {"id": 2584, "code": "NTS3032", "section": "002", "url": "u2584"},
            {"id": 2604, "code": "SOC3010", "section": "002", "url": "u2604"},
            {"id": 2612, "code": "SOC3030", "section": "002", "url": "u2612"},
        ]
        known = {  # titles already taken from the assignment / quiz index pages
            2584: {fetch.norm_title("MCQS-9")},
            2612: {fetch.norm_title("Assignment#3")},
            2604: {fetch.norm_title("[QUIZ 4] FOUR ROOMS — completion code 2%")},
        }
        tasks = fetch.calendar_tasks(self.events, fetch.course_resolver(courses, "cal"), known)
        # dropped: attendance event, the three known items, the course of another semester
        self.assertEqual(tasks, [{
            "id": "event-66040", "type": "event", "courseId": 2604,
            "title": "Choice Midterm Paper Review Sessions closes",
            "due": "2026-12-02", "allDay": True, "status": "unknown", "url": "u2604",
        }])

    def test_calendar_only_tasks(self):
        tasks = fetch.calendar_tasks(self.events, lambda event: ("c", "cal"), {})
        by_id = {t["id"]: t for t in tasks}
        self.assertNotIn("event-65971", by_id)  # attendance
        # timed window -> deadline is its end, in Tashkent time
        self.assertEqual(by_id["event-63124"]["due"], "2026-11-20T23:59:00+05:00")
        # "(Quiz closes)" suffix stripped, the doubled space of the export collapsed
        self.assertEqual((by_id["event-65286"]["type"], by_id["event-65286"]["title"]),
                         ("quiz", "[QUIZ 4] FOUR ROOMS — completion code 2%"))


class Helpers(unittest.TestCase):
    def test_times(self):
        self.assertEqual(fetch.local_iso("2026-11-20T23:55"), "2026-11-20T23:55:00+05:00")
        self.assertIsNone(fetch.local_iso(None))
        self.assertEqual(fetch.period_times(1, 3), {"start": "08:00", "end": "09:30"})
        self.assertEqual(fetch.period_times(13, 15), {"start": "14:00", "end": "15:30"})

    def test_grade_group(self):
        cases = [
            ("MidTerm Marks", "assign", None, "midterm"),  # a mark kept as an assignment
            ("Mid-term", "assign", None, "midterm"),
            ("Quiz#1 Marks", "assign", None, "quiz"),
            ("MCQS-9", "quiz", None, "quiz"),
            ("Final Exam (100)", "manual", None, "final"),
            ("Final project report", "assign", None, "homework"),
            ("Homework 3 - B+ trees", "assign", None, "homework"),
            ("Offline Attendance", "manual", "Attendance", "attendance"),
            ("Assign", "category", "Assign", "homework"),
            ("Term paper", "manual", "Term Project", "other"),
        ]
        for name, kind, category, expected in cases:
            self.assertEqual(fetch.grade_group(name, kind, category), expected, name)

    def test_course_stats(self):
        url = f"{BASE}/grade/report/user/index.php?id=2604"
        report = parsers.parse_grade_report(fixture("grade_report.html"), url)
        assignments = [{"cmid": 73195, "title": "Homework Assignment 3", "grade": None},
                       {"cmid": 1, "title": "Homework X", "grade": 4.75}]
        quizzes = [{"cmid": 70126, "title": "Quiz 1", "grade": 0.9, "max": 1.0},  # already in the report
                   {"cmid": 2, "title": "MCQS-1", "grade": 0.6, "max": 1.0}]
        stats = fetch.course_stats(2604, assignments, quizzes, report, {"present": 1})
        self.assertEqual((stats["courseId"], stats["gradesHidden"], stats["attendance"]), (2604, False, {"present": 1}))
        self.assertEqual([(g["name"], g["group"], g["taskId"], g["grade"], g["max"]) for g in stats["grades"]], [
            ("Midterm exam", "midterm", None, 20.0, 40.0),  # category sum with a value
            ("Offline Attendance", "attendance", None, 20.0, 26.0),
            ("Attendance (10%)", "attendance", None, 7.69, 10.0),
            # empty "Attendance" / "Etc." sums and the empty course total are dropped
            ("[SOC3010-002] Quiz 1 (1%)", "quiz", "quiz-70126", 90.0, 100.0),
            ("[SOC3010-002] Homework Assignment 3 (2.5%)", "homework", "assign-73195", None, 100.0),
            ("Midterm Manual", "midterm", None, 12.0, 25.0),
            ("Homework X", "homework", "assign-1", 4.75, None),  # only on the index page
            ("MCQS-1", "quiz", "quiz-2", 0.6, 1.0),
        ])
        hidden = fetch.course_stats(2608, assignments, quizzes, None, None)
        self.assertTrue(hidden["gradesHidden"])
        self.assertEqual([g["taskId"] for g in hidden["grades"]], ["assign-1", "quiz-70126", "quiz-2"])

    def test_task_status(self):
        base = {"cmid": 1, "title": "t", "due": None, "url": "u"}
        self.assertEqual(fetch.assign_task(7, {**base, "submitted": False, "graded": False})["status"], "pending")
        self.assertEqual(fetch.assign_task(7, {**base, "submitted": True, "graded": False})["status"], "submitted")
        self.assertEqual(fetch.assign_task(7, {**base, "submitted": False, "graded": True})["status"], "graded")
        self.assertEqual(fetch.quiz_task(7, {**base, "graded": False})["status"], "pending")


if __name__ == "__main__":
    unittest.main()
