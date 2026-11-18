(() => {
  'use strict';

  const TZ = 'Asia/Tashkent';
  const TZ_OFFSET = '+05:00';
  const DONE_KEY = 'eclass-board:done';
  const PASS_KEY = 'eclass-board:passphrase';
  const STALE_MS = 6 * 3600e3;          // data older than this gets a warning
  const OVERDUE_WINDOW_MS = 7 * 86400e3; // how long an unsubmitted task stays "actual" after its deadline
  const REFETCH_MS = 10 * 60e3;
  // Course identity colors: a validated categorical palette, in its fixed order. Red is
  // left out on purpose (it means "overdue" here), and text never wears these colors:
  // they mark a dot, a bar or an avatar next to the course name.
  const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6'];
  const SERIES_ON = { '#eda100': '#0b0b0b' }; // label color inside a fill; white on the others
  const STOP_WORDS = new Set(['the', 'of', 'and', 'to', 'in', 'for', 'a', 'an']);
  const ICONS = {
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    pin: '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
    chevron: '<path d="M6 9l6 6 6-6"/>',
  };

  const TYPE_LABEL = { assign: 'Задание', quiz: 'Квиз', event: 'Событие' };
  const GROUPS = [
    ['overdue', 'Просрочено'],
    ['today', 'Сегодня'],
    ['tomorrow', 'Завтра'],
    ['week', 'Ближайшие 7 дней'],
    ['later', 'Позже'],
    ['nodate', 'Без дедлайна'],
    ['stale', 'Просрочено больше недели назад'],
    ['done', 'Сдано или отмечено'],
  ];
  const ACTUAL_GROUPS = new Set(['overdue', 'today', 'tomorrow', 'week', 'later']);
  const TABS = ['hw', 'schedule', 'stats'];
  const STAT_GROUPS = [
    ['homework', 'Домашки'],
    ['quiz', 'Квизы'],
    ['midterm', 'Мидтерм'],
    ['final', 'Файнал'],
    ['other', 'Прочее'],
  ];
  const ATTENDANCE_LABEL = { absent: 'пропуск', late: 'опоздание', other: 'другое' };
  const DAY_NAMES = ['', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
  const WEEKDAY_INDEX = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 };

  const fmt = (options) => new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, ...options });
  const fmtDateTime = fmt({ weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  const fmtDate = fmt({ weekday: 'short', day: 'numeric', month: 'short' });
  const fmtDateTimeYear = fmt({ day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const fmtDateYear = fmt({ day: 'numeric', month: 'short', year: 'numeric' });
  const fmtYear = fmt({ year: 'numeric' });
  const fmtUpdated = fmt({ day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  const fmtToday = fmt({ weekday: 'long', day: 'numeric', month: 'long' });
  const fmtDayKey = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
  const fmtWeekday = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'short' });
  const fmtClock = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

  const state = {
    data: null,
    envelope: null, // data.json as fetched, when it is encrypted
    passphrase: loadPassphrase(),
    locked: false, // encrypted data and no working passphrase yet
    unlockError: null,
    error: null,
    loading: false,
    loadedAt: 0,
    tab: TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'hw',
    filter: 'actual',
    done: loadDone(),
    openCourses: new Set(), // expanded cards of the statistics tab survive re-renders
  };

  const $ = (id) => document.getElementById(id);

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else node.setAttribute(key, value === true ? '' : value);
    }
    node.append(...children.filter((child) => child !== null && child !== undefined));
    return node;
  }

  function icon(name, cls) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    if (cls) svg.setAttribute('class', cls);
    svg.innerHTML = ICONS[name]; // static markup from ICONS only
    return svg;
  }

  const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);

  // Russian plural: 1 задача, 2 задачи, 5 задач.
  function plural(n, one, few, many) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  }

  function loadDone() {
    try {
      return new Set(JSON.parse(localStorage.getItem(DONE_KEY) || '[]'));
    } catch {
      return new Set();
    }
  }

  function saveDone() {
    try {
      localStorage.setItem(DONE_KEY, JSON.stringify([...state.done]));
    } catch {
      // private mode: marks live until the page is closed
    }
  }

  function loadPassphrase() {
    try {
      return localStorage.getItem(PASS_KEY) || '';
    } catch {
      return '';
    }
  }

  function savePassphrase(value) {
    try {
      if (value) localStorage.setItem(PASS_KEY, value);
      else localStorage.removeItem(PASS_KEY);
    } catch {
      // private mode: the passphrase is asked again on the next visit
    }
  }

  /* ---------- encrypted data.json (see scraper/envelope.py) ---------- */

  // PBKDF2-SHA256 -> AES-256-GCM, with the parameters stored in the file itself.
  // Deriving the key is slow on purpose, so it is reused while the file's salt stays the same.
  let keyCache = null;

  const fromBase64 = (text) => Uint8Array.from(atob(text), (char) => char.charCodeAt(0));

  async function decrypt(envelope, passphrase) {
    if (!window.crypto || !window.crypto.subtle) {
      throw new Error('браузер не может расшифровать данные: сайт должен открываться по HTTPS');
    }
    if (envelope.v !== 1) throw new Error(`неизвестная версия шифрования (${envelope.v})`);
    const cacheId = `${envelope.salt}:${envelope.iterations}:${passphrase}`;
    if (!keyCache || keyCache.id !== cacheId) {
      const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(passphrase), 'PBKDF2', false, ['deriveKey']);
      const key = await crypto.subtle.deriveKey(
        { name: 'PBKDF2', hash: 'SHA-256', salt: fromBase64(envelope.salt), iterations: envelope.iterations },
        base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
      keyCache = { id: cacheId, key };
    }
    const plain = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: fromBase64(envelope.iv) }, keyCache.key, fromBase64(envelope.data));
    return JSON.parse(new TextDecoder().decode(plain));
  }

  function lock(message) {
    state.data = null;
    state.locked = true;
    state.unlockError = message;
    state.passphrase = '';
    savePassphrase('');
    keyCache = null;
  }

  // false on a wrong passphrase: WebCrypto rejects the GCM tag with an OperationError.
  async function unlock(passphrase) {
    try {
      state.data = await decrypt(state.envelope, passphrase);
    } catch (error) {
      if (error.name !== 'OperationError') throw error;
      lock(passphrase === state.passphrase
        ? 'Сохранённый пароль не подошёл — возможно, DATA_PASSPHRASE сменили.'
        : 'Пароль не подошёл.');
      return false;
    }
    state.locked = false;
    state.unlockError = null;
    state.passphrase = passphrase;
    savePassphrase(passphrase);
    return true;
  }

  /* ---------- time helpers (everything in Tashkent time, like eClass) ---------- */

  const dayNumber = (date) => Date.parse(fmtDayKey.format(date)) / 86400e3;

  function dueOf(task) {
    if (!task.due) return null;
    const date = new Date(task.allDay ? `${task.due}T23:59:59${TZ_OFFSET}` : task.due);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatDue(task, due, now) {
    const otherYear = fmtYear.format(due) !== fmtYear.format(now);
    if (task.allDay) return (otherYear ? fmtDateYear : fmtDate).format(due);
    return (otherYear ? fmtDateTimeYear : fmtDateTime).format(due);
  }

  function span(ms) {
    const minutes = Math.round(Math.abs(ms) / 60e3);
    const d = Math.floor(minutes / 1440);
    const h = Math.floor((minutes % 1440) / 60);
    const m = minutes % 60;
    if (d >= 7) return `${d} дн`;
    if (d >= 1) return `${d} дн ${h} ч`;
    if (h >= 1) return `${h} ч ${m} мин`;
    return `${m} мин`;
  }

  const toMinutes = (clock) => Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3, 5));

  function clockOf(now) {
    const [hours, minutes] = fmtClock.format(now).split(':').map(Number);
    return { today: WEEKDAY_INDEX[fmtWeekday.format(now)], nowMinutes: hours * 60 + minutes };
  }

  function safeUrl(url) {
    try {
      return new URL(url).protocol === 'https:' ? url : null;
    } catch {
      return null;
    }
  }

  /* ---------- homework ---------- */

  function classify(task, now) {
    const due = dueOf(task);
    const auto = task.status === 'submitted' || task.status === 'graded';
    const done = auto || state.done.has(task.id);
    let group;
    if (done) group = 'done';
    else if (!due) group = 'nodate';
    else if (due < now) group = now - due <= OVERDUE_WINDOW_MS ? 'overdue' : 'stale';
    else {
      const days = dayNumber(due) - dayNumber(now);
      group = days <= 0 ? 'today' : days === 1 ? 'tomorrow' : days <= 7 ? 'week' : 'later';
    }
    return { task, due, auto, done, group };
  }

  // "Operating System" -> OS, "Introduction to Economics" -> IE, "Database" -> Da.
  function initials(name) {
    const words = name.split(/[^\p{L}\p{N}]+/u).filter((word) => word && !STOP_WORDS.has(word.toLowerCase()));
    if (!words.length) return '?';
    if (words.length === 1) return capitalize(words[0].slice(0, 2));
    return (words[0][0] + words[1][0]).toUpperCase();
  }

  // Colors follow the course's place in the eClass course list, which is fixed for the semester.
  function courseInfo(courseId) {
    const index = state.data.courses.findIndex((course) => course.id === courseId);
    if (index < 0) return null;
    const course = state.data.courses[index];
    const color = SERIES[index % SERIES.length];
    return { ...course, color, on: SERIES_ON[color] || '#ffffff', initials: initials(course.name) };
  }

  function taskCard(item, now) {
    const { task, due, auto, done, group } = item;
    const course = courseInfo(task.courseId);
    const url = safeUrl(task.url);

    const checkbox = el('input', {
      type: 'checkbox',
      checked: done,
      disabled: auto,
      'aria-label': `Сделано: ${task.title}`,
      title: auto ? 'Сдано в eClass' : 'Отметить как сделанное (хранится в этом браузере)',
    });
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) state.done.add(task.id);
      else state.done.delete(task.id);
      saveDone();
      render();
    });

    const title = url
      ? el('a', { class: 'title', href: url, target: '_blank', rel: 'noopener', text: task.title })
      : el('span', { class: 'title', text: task.title });

    const meta = el('div', { class: 'meta' },
      el('span', { class: 'dot' }),
      el('span', { class: 'course', title: course ? course.name : null, text: course ? course.name : 'Без курса' }),
      course && course.professor ? el('span', { class: 'prof', text: `· ${course.professor}` }) : null);

    // The pill says where the task stands; its color backs up the words, never replaces them.
    let pill = null;
    if (task.status === 'graded') pill = ['оценено', 'good'];
    else if (task.status === 'submitted') pill = ['сдано', 'good'];
    else if (done) pill = ['отмечено', 'good'];
    else if (due && due < now) pill = [`просрочено на ${span(now - due)}`, 'critical'];
    else if (due) pill = [`через ${span(due - now)}`, group === 'today' || group === 'tomorrow' ? 'warning' : ''];
    const kind = TYPE_LABEL[task.type] || 'Задача';
    const foot = el('div', { class: 'foot' },
      el('span', { class: 'when', text: `${kind} · ${due ? formatDue(task, due, now) : 'без срока'}` }),
      pill ? el('span', { class: pill[1] ? `pill ${pill[1]}` : 'pill', text: pill[0] }) : null);

    return el('article', { class: `task ${group}`, style: course ? `--course: ${course.color}` : null },
      el('label', { class: 'check' }, checkbox),
      el('div', { class: 'body' }, title, meta, foot));
  }

  function heroRow(iconName, label, value, sub) {
    return el('div', { class: 'hero-row' }, icon(iconName),
      el('div', {},
        el('span', { class: 'k', text: label }),
        el('span', { class: 'v', text: value }),
        sub ? el('span', { class: 's', text: sub }) : null));
  }

  // The next class: the one running now, the next one today, or the first one on a later day.
  function nextClass(now) {
    const slots = state.data.schedule || [];
    const { today, nowMinutes } = clockOf(now);
    for (let offset = 0; offset < 7; offset += 1) {
      const day = ((today - 1 + offset) % 7) + 1;
      const candidates = slots
        .filter((slot) => slot.day === day && (offset > 0 || toMinutes(slot.end) > nowMinutes))
        .sort((a, b) => a.from - b.from);
      if (candidates.length) {
        const slot = candidates[0];
        return { slot, offset, running: offset === 0 && toMinutes(slot.start) <= nowMinutes };
      }
    }
    return null;
  }

  // The one number this view leads with: how many tasks need attention, then the next deadline and class.
  function renderHero(items, now) {
    const hero = $('hw-hero');
    hero.replaceChildren();
    const actual = items.filter((item) => ACTUAL_GROUPS.has(item.group));
    const overdue = items.filter((item) => item.group === 'overdue').length;
    const next = actual.filter((item) => item.due && item.due >= now).sort((a, b) => a.due - b.due)[0];
    const lesson = nextClass(now);

    const rows = [];
    if (next) {
      const course = courseInfo(next.task.courseId);
      rows.push(heroRow('clock', 'Ближайший дедлайн', next.task.title,
        [course ? course.name : null, formatDue(next.task, next.due, now), `через ${span(next.due - now)}`]
          .filter(Boolean).join(' · ')));
    }
    if (lesson) {
      const course = courseInfo(lesson.slot.courseId);
      const when = lesson.running ? 'Сейчас идёт пара'
        : `Следующая пара · ${lesson.offset === 0 ? 'сегодня' : lesson.offset === 1 ? 'завтра' : DAY_NAMES[lesson.slot.day].toLowerCase()}`;
      rows.push(heroRow('pin', when, course ? course.name : 'Пара',
        [`${lesson.slot.start}–${lesson.slot.end}`, lesson.slot.room].filter(Boolean).join(' · ')));
    }

    hero.append(
      el('div', { class: 'hero-label', text: 'Актуально сейчас' }),
      el('div', { class: 'hero-figure' },
        el('strong', { text: String(actual.length) }),
        el('span', { text: plural(actual.length, 'задача', 'задачи', 'задач') }),
        overdue ? el('em', { class: 'hero-flag', text: `${overdue} ${plural(overdue, 'просрочена', 'просрочены', 'просрочено')}` }) : null),
      rows.length ? el('div', { class: 'hero-rows' }, ...rows) : null);
  }

  function renderHomework(now) {
    const list = $('hw-list');
    list.replaceChildren();
    const items = state.data.tasks.map((task) => classify(task, now));
    const actualCount = items.filter((item) => ACTUAL_GROUPS.has(item.group)).length;

    const badge = $('hw-count');
    badge.hidden = actualCount === 0;
    badge.textContent = actualCount;
    badge.classList.toggle('alert', items.some((item) => item.group === 'overdue'));
    renderHero(items, now);

    for (const button of document.querySelectorAll('[data-filter]')) {
      button.setAttribute('aria-pressed', String(button.dataset.filter === state.filter));
    }

    const byTime = (a, b) => (a.due ? a.due.getTime() : Infinity) - (b.due ? b.due.getTime() : Infinity);
    let shown = 0;
    for (const [group, label] of GROUPS) {
      if (state.filter === 'actual' && !ACTUAL_GROUPS.has(group)) continue;
      const members = items.filter((item) => item.group === group).sort(byTime);
      if (!members.length) continue;
      if (group === 'done' || group === 'stale') members.reverse(); // most recent first
      shown += members.length;
      list.append(el('section', { class: `group ${group}` },
        el('h2', {}, label, el('span', { class: 'count', text: members.length })),
        ...members.map((item) => taskCard(item, now))));
    }

    if (!shown) {
      const text = state.filter === 'actual' && items.length
        ? 'Актуальных задач нет. Всё остальное — во вкладке «Все».'
        : 'Задач нет.';
      list.append(el('p', { class: 'empty', text }));
    }
  }

  /* ---------- schedule ---------- */

  function renderSchedule(now) {
    const root = $('schedule');
    root.replaceChildren();
    const slots = state.data.schedule;
    if (!slots.length) {
      const text = state.data.source === 'ical'
        ? 'Расписание доступно только в режиме с логином (ECLASS_USERNAME / ECLASS_PASSWORD).'
        : 'Расписания нет.';
      root.append(el('p', { class: 'empty', text }));
      return;
    }

    const { today, nowMinutes } = clockOf(now);

    const lastDay = Math.max(5, ...slots.map((slot) => slot.day));
    let days = Array.from({ length: lastDay }, (_, i) => i + 1);
    // On a phone the days stack, so start from today instead of Monday.
    if (window.matchMedia('(max-width: 560px)').matches && days.includes(today)) {
      days = [...days.slice(today - 1), ...days.slice(0, today - 1)];
    }

    const grid = el('div', { class: 'days' });
    for (const day of days) {
      const daySlots = slots.filter((slot) => slot.day === day).sort((a, b) => a.from - b.from);
      const isToday = day === today;
      const nextIndex = isToday ? daySlots.findIndex((slot) => toMinutes(slot.start) > nowMinutes) : -1;
      const column = el('section', { class: `day${isToday ? ' is-today' : ''}` },
        el('h2', {}, DAY_NAMES[day], isToday ? el('span', { class: 'tag', text: 'сегодня' }) : null));
      if (!daySlots.length) column.append(el('p', { class: 'free', text: 'Пар нет' }));
      daySlots.forEach((slot, index) => {
        const course = courseInfo(slot.courseId);
        const running = isToday && toMinutes(slot.start) <= nowMinutes && nowMinutes < toMinutes(slot.end);
        const tag = running ? el('span', { class: 'tag now', text: 'идёт' })
          : index === nextIndex ? el('span', { class: 'tag next', text: 'далее' }) : null;
        const periods = slot.from === slot.to ? `период ${slot.from}` : `периоды ${slot.from}–${slot.to}`;
        column.append(el('article', { class: 'slot', title: periods, style: course ? `--course: ${course.color}` : null },
          el('div', { class: 'time' }, slot.start, el('small', { text: slot.end })),
          el('div', { class: 'bar' }),
          el('div', { class: 'info' },
            el('div', { class: 'name' }, course ? course.name : 'Курс', tag),
            el('div', { class: 'sub', text: [course && course.professor, slot.room].filter(Boolean).join(' · ') }))));
      });
      grid.append(column);
    }
    root.append(grid);
  }

  /* ---------- statistics ---------- */

  const fmtNumber = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
  const gradeText = (grade, max) => (max ? `${fmtNumber.format(grade)} / ${fmtNumber.format(max)}` : fmtNumber.format(grade));

  // One row per graded thing of a course: tasks (with their grade, if any) plus
  // gradebook items that are not tasks (manual marks, category sums).
  function courseRows(course, stat, now) {
    const grades = stat ? stat.grades : [];
    const byTask = new Map(grades.filter((grade) => grade.taskId).map((grade) => [grade.taskId, grade]));
    const rows = [];
    const taskIds = new Set();
    for (const task of state.data.tasks) {
      if (task.courseId !== course.id || !task.group) continue;
      taskIds.add(task.id);
      const grade = byTask.get(task.id);
      rows.push({
        title: task.title, group: task.group, kind: task.type, url: safeUrl(task.url), item: classify(task, now),
        grade: grade ? grade.grade : null, max: grade ? grade.max : null,
      });
    }
    for (const grade of grades) {
      if (grade.taskId && taskIds.has(grade.taskId)) continue;
      rows.push({
        title: grade.name, group: grade.group, kind: grade.kind, url: null, item: null, grade: grade.grade, max: grade.max,
      });
    }
    return rows;
  }

  function rowValue(row, now) {
    if (row.grade !== null && row.grade !== undefined) return { text: gradeText(row.grade, row.max), cls: '' };
    if (!row.item) return { text: row.max ? `— / ${fmtNumber.format(row.max)}` : '—', cls: 'muted' };
    const { task, due } = row.item;
    if (task.status === 'submitted') return { text: 'сдано, оценки нет', cls: 'muted' };
    if (task.status === 'graded') return { text: 'оценено', cls: 'muted' };
    if (state.done.has(task.id)) return { text: 'отмечено сделанным', cls: 'muted' };
    if (due && due < now) return { text: 'пропущено', cls: 'missed' };
    if (due) return { text: `до ${fmtDate.format(due)}`, cls: 'muted' };
    return { text: 'не сдано', cls: 'muted' };
  }

  function statRow(title, value, cls, url) {
    const name = url
      ? el('a', { class: 'name', href: url, target: '_blank', rel: 'noopener', text: title })
      : el('span', { class: 'name', text: title });
    return el('div', { class: 'stat-row' }, name, el('span', { class: `value ${cls}`, text: value }));
  }

  const percent = (part, whole) => `${Math.round((part / whole) * 100)}%`;

  function sumRow(label, grade, max) {
    return el('div', { class: 'stat-row sum' },
      el('span', { class: 'name', text: label }),
      el('span', { class: 'value', text: gradeText(grade, max) + (max ? ` · ${percent(grade, max)}` : '') }));
  }

  function statSection(title, ...children) {
    return el('section', { class: 'stat-section' }, el('h3', { text: title }), ...children);
  }

  function courseCard(course, stat, now) {
    const rows = courseRows(course, stat, now);
    const attendance = stat ? stat.attendance : null;
    const sessions = attendance ? attendance.present + attendance.absent + attendance.late + attendance.other : 0;
    const attended = attendance ? attendance.present + attendance.late : 0;
    const homework = rows.filter((row) => row.group === 'homework' && row.kind !== 'category');
    const missed = homework.filter((row) => rowValue(row, now).cls === 'missed').length;
    const handedIn = homework.filter((row) => row.grade !== null || (row.item && row.item.done)).length;
    // Average quiz score; only quizzes whose maximum is known can be put on one scale.
    const quizScored = rows.filter((row) => row.group === 'quiz' && row.kind !== 'category' && row.grade !== null && row.max);
    const total = rows.find((row) => row.group === 'total');

    // Meter list: one ratio per row against its own whole. The fill carries severity;
    // the thresholds are visual cues, not university rules.
    const meters = el('div', { class: 'meters' });
    const severity = (ratio) => (ratio < 0.5 ? 'critical' : ratio < 0.75 ? 'warning' : '');
    const meter = (label, ratio, figure, tone, title) => meters.append(el('div', { class: tone ? `meter ${tone}` : 'meter', title },
      el('span', { class: 'label', text: label }),
      el('span', { class: 'track' },
        el('span', { class: 'fill', style: `width: ${Math.round(Math.min(1, Math.max(0, ratio)) * 100)}%` })),
      el('span', { class: 'figure', text: figure })));
    if (sessions) {
      meter('Посещаемость', attended / sessions, percent(attended, sessions), severity(attended / sessions),
        `${attended} из ${sessions} занятий`);
    }
    if (quizScored.length) {
      const sum = quizScored.reduce((acc, row) => acc + row.grade, 0);
      const max = quizScored.reduce((acc, row) => acc + row.max, 0);
      meter('Квизы', sum / max, percent(sum, max), severity(sum / max),
        `${fmtNumber.format(sum)} из ${fmtNumber.format(max)} баллов`);
    }
    if (homework.length) {
      meter('Домашки', handedIn / homework.length, `${handedIn} из ${homework.length}`, missed ? 'warning' : '',
        'сдано или оценено в eClass, либо отмечено галочкой');
    }

    const chips = el('div', { class: 'chips' });
    if (missed) chips.append(el('span', { class: 'pill critical', text: `Пропущено: ${missed}` }));
    if (total && total.grade !== null) chips.append(el('span', { class: 'pill', text: `Итог ${gradeText(total.grade, total.max)}` }));

    const body = el('div', { class: 'card-body' });

    const attendanceRows = rows.filter((row) => row.group === 'attendance');
    if (sessions || attendanceRows.length) {
      const section = statSection('Посещаемость');
      if (sessions) {
        section.append(statRow('Был на занятиях', `${attended} из ${sessions} · ${percent(attended, sessions)}`, ''));
        for (const session of attendance.missed) {
          const date = new Date(`${session.date}T12:00:00${TZ_OFFSET}`);
          section.append(statRow(fmtDate.format(date), ATTENDANCE_LABEL[session.status] || session.status,
            session.status === 'late' ? 'late' : 'missed'));
        }
      }
      for (const row of attendanceRows) {
        const value = rowValue(row, now);
        section.append(statRow(row.title, value.text, value.cls));
      }
      body.append(section);
    }

    for (const [group, title] of STAT_GROUPS) {
      const members = rows.filter((row) => row.group === group);
      if (!members.length) continue;
      // A gradebook category sum is the section's own total: it goes under the items
      // instead of a sum counted here, which would add it to them a second time.
      const items = members.filter((row) => row.kind !== 'category');
      const totals = members.filter((row) => row.kind === 'category');
      const section = statSection(title);
      for (const row of items.length ? items : totals) {
        const value = rowValue(row, now);
        section.append(statRow(row.title, value.text, value.cls, row.url));
      }
      const graded = items.filter((row) => row.grade !== null);
      if (items.length && totals.length) {
        for (const row of totals) section.append(sumRow('Итого в eClass', row.grade, row.max));
      } else if (graded.length > 1) {
        const sum = graded.reduce((acc, row) => acc + row.grade, 0);
        const max = graded.every((row) => row.max) ? graded.reduce((acc, row) => acc + row.max, 0) : null;
        section.append(sumRow('Сумма баллов', sum, max));
      }
      body.append(section);
    }

    if (total && total.grade !== null) {
      body.append(statSection('Итог курса', statRow('Course total', gradeText(total.grade, total.max), '')));
    }
    if (!body.children.length) body.append(el('p', { class: 'card-note', text: 'В eClass по этому курсу пока нет ни оценок, ни отметок посещаемости.' }));
    if (stat && stat.gradesHidden) {
      body.append(el('p', { class: 'card-note', text: 'Преподаватель скрыл отчёт по оценкам — показано только то, что видно на страницах заданий и квизов.' }));
    }

    const head = el('div', { class: 'card-head' },
      el('span', { class: 'avatar', 'aria-hidden': 'true', text: course.initials }),
      el('div', {},
        el('div', { class: 'cname', text: course.name }),
        course.professor ? el('div', { class: 'prof', text: course.professor }) : null),
      icon('chevron', 'chev'));
    const card = el('details', {
      class: 'course-card',
      open: state.openCourses.has(course.id),
      style: `--course: ${course.color}; --course-on: ${course.on}`,
    },
      el('summary', {},
        head,
        meters.children.length ? meters : el('div', { class: 'card-quiet', text: 'Оценок и посещаемости пока нет' }),
        chips.children.length ? chips : null),
      body);
    card.addEventListener('toggle', () => {
      if (card.open) state.openCourses.add(course.id);
      else state.openCourses.delete(course.id);
    });
    return card;
  }

  function renderStats(now) {
    const root = $('stats');
    root.replaceChildren();
    const stats = state.data.stats || [];
    if (!stats.length) {
      const text = state.data.privateOmitted
        ? 'Оценки и посещаемость открыто не публикуются. Добавь в репозиторий секрет DATA_PASSPHRASE — данные сайта зашифруются паролем, и здесь появится статистика.'
        : state.data.source === 'ical'
          ? 'Статистика доступна только в режиме с логином (ECLASS_USERNAME / ECLASS_PASSWORD).'
          : 'Статистики пока нет.';
      root.append(el('p', { class: 'empty', text }));
      return;
    }
    for (const course of state.data.courses) {
      root.append(courseCard(courseInfo(course.id), stats.find((stat) => stat.courseId === course.id) || null, now));
    }
    root.append(el('p', {
      class: 'stats-note',
      text: '«Пропущено» — дедлайн прошёл, а в eClass нет ни сдачи, ни оценки. Если сдавал в другом месте (например, в Gradescope), отметь задачу галочкой во вкладке «Домашки».',
    }));
  }

  /* ---------- page ---------- */

  function renderStatus(now) {
    const status = $('status');
    status.replaceChildren();
    const data = state.data;
    if (!data || data.setupRequired) return;
    const generated = new Date(data.generatedAt);
    const age = now - generated;
    const line = el('div', {}, `Обновлено ${fmtUpdated.format(generated)} (${span(age)} назад)`);
    if (age > STALE_MS) {
      line.append(' · ', el('span', { class: 'stale', text: 'данные устарели — проверь workflow в GitHub Actions' }));
    }
    // A home-screen web app on iOS has no reload button, so the page brings its own.
    const refresh = el('button', {
      type: 'button', class: 'refresh', disabled: state.loading,
      text: state.loading ? 'Обновляю…' : 'Обновить',
    });
    refresh.addEventListener('click', load);
    const actions = el('div', { class: 'status-actions' }, refresh);
    if (state.envelope) {
      const forget = el('button', { type: 'button', class: 'refresh', text: 'Забыть пароль' });
      forget.addEventListener('click', () => {
        lock(null);
        render();
      });
      actions.append(forget);
    }
    status.append(el('div', { class: 'status-row' }, line, actions));
    if (data.source === 'ical') {
      status.append(el('div', { text: 'Режим календаря: без статуса сдачи и точного времени дедлайнов.' }));
    }
    if (data.warnings && data.warnings.length) {
      status.append(el('ul', {}, ...data.warnings.map((warning) => el('li', { text: warning }))));
    }
  }

  function clearPanels() {
    for (const id of ['hw-hero', 'hw-list', 'schedule', 'stats', 'status']) $(id).replaceChildren();
    $('hw-count').hidden = true;
  }

  function render() {
    $('today').textContent = capitalize(fmtToday.format(new Date()));
    for (const button of document.querySelectorAll('[data-tab]')) {
      if (button.dataset.tab === state.tab) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    }
    const locked = state.locked;
    document.querySelector('.tabs').hidden = locked;
    for (const tab of TABS) $(`panel-${tab}`).hidden = locked || state.tab !== tab;

    // The form is only toggled, never rebuilt: a re-render must not wipe a half-typed passphrase.
    $('unlock').hidden = !locked;
    $('unlock-error').hidden = !state.unlockError;
    $('unlock-error').textContent = state.unlockError || '';

    const notice = $('notice');
    const data = state.data;
    if (locked) {
      notice.hidden = !state.error;
      if (state.error) notice.textContent = `Не удалось загрузить данные (${state.error.message}).`;
      clearPanels();
      return;
    }
    if (!data || data.setupRequired) {
      notice.hidden = false;
      notice.textContent = data
        ? 'Данные ещё не подключены: добавь секреты ECLASS_USERNAME и ECLASS_PASSWORD в настройках репозитория (Settings → Secrets and variables → Actions) и запусти workflow.'
        : state.error ? `Не удалось загрузить data.json (${state.error.message}).` : 'Загрузка…';
      clearPanels();
      return;
    }
    notice.hidden = !state.error;
    if (state.error) notice.textContent = `Не удалось обновить данные (${state.error.message}) — показана прошлая версия.`;

    const now = new Date();
    renderHomework(now);
    renderSchedule(now);
    renderStats(now);
    renderStatus(now);
  }

  async function load() {
    if (state.loading) return;
    state.loading = true;
    render();
    try {
      const response = await fetch(`data.json?ts=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const json = await response.json();
      if (json.encrypted) {
        state.envelope = json;
        if (state.passphrase) await unlock(state.passphrase);
        else lock(null);
      } else {
        state.envelope = null;
        state.locked = false;
        state.data = json;
      }
      state.error = null;
    } catch (error) {
      state.error = error;
    }
    state.loading = false;
    state.loadedAt = Date.now();
    render();
  }

  $('unlock').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = $('unlock-pass');
    const button = $('unlock').querySelector('button[type="submit"]');
    if (!state.envelope || !input.value) return;
    button.disabled = true;
    button.textContent = 'Проверяю…';
    try {
      if (await unlock(input.value)) {
        input.value = '';
        input.blur(); // hides the phone keyboard
      }
    } catch (error) {
      state.unlockError = error.message;
    }
    button.disabled = false;
    button.textContent = 'Открыть';
    render();
  });

  for (const button of document.querySelectorAll('[data-tab]')) {
    button.addEventListener('click', () => {
      state.tab = button.dataset.tab;
      history.replaceState(null, '', state.tab === 'hw' ? location.pathname + location.search : `#${state.tab}`);
      window.scrollTo(0, 0);
      render();
    });
  }
  for (const button of document.querySelectorAll('[data-filter]')) {
    button.addEventListener('click', () => {
      state.filter = button.dataset.filter;
      render();
    });
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    if (Date.now() - state.loadedAt > REFETCH_MS) load();
    else render();
  });
  setInterval(render, 60e3); // keeps "через 3 ч" and the current class fresh

  load();
})();
