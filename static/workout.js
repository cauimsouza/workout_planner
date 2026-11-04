// Workout session wizard: walks through the exercises of a workout day one at a time.
//
// It runs in the browser so it keeps working offline. Program and history come from
// /api/program (cached by the service worker). The session in progress and any sessions
// not yet uploaded are kept in localStorage. Each time an exercise is completed, the whole
// session is uploaded to /api/workout-sessions, an idempotent upsert keyed by client_id.

(() => {
    const ACTIVE_KEY = 'workout-active-session';
    const UNSYNCED_KEY = 'workout-unsynced-sessions';
    const HISTORY_PAGE = 10;
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    let data = null;          // Response of /api/program, plus local unsynced sessions merged in
    let active = null;        // Session in progress
    let status = { text: '', kind: '' };
    let historyShown = HISTORY_PAGE;

    // --- Helpers ---

    function load(key, fallback) {
        try {
            const value = localStorage.getItem(key);
            return value ? JSON.parse(value) : fallback;
        } catch (e) {
            return fallback;
        }
    }

    function store(key, value) {
        try {
            if (value === null) localStorage.removeItem(key);
            else localStorage.setItem(key, JSON.stringify(value));
        } catch (e) {
            // Storage unavailable (private mode, quota): the app still works for this page load.
        }
    }

    function esc(value) {
        return String(value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function fmt(n) {
        return n === null || n === undefined || n === '' ? '—' : String(parseFloat(Number(n).toFixed(3)));
    }

    function todayISO() {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

    // Dates are compared and shown by their YYYY-MM-DD part only, which is the day the user picked.
    function dayOf(isoString) {
        return isoString.slice(0, 10);
    }

    // "4 Nov 2026"
    function formatDay(isoString) {
        return window.formatLongDate(dayOf(isoString));
    }

    // "28 Oct", or "28 Oct 2025" if not this year. For tight spots like the "Last time" tile.
    function formatShortDay(isoString) {
        const long = formatDay(isoString);
        const year = String(new Date().getFullYear());
        return long.endsWith(` ${year}`) ? long.slice(0, -5) : long;
    }

    function newClientId() {
        if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
        return `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }

    function weeks() {
        return data ? data.cycle_weeks : 4;
    }

    // Position of a (cycle, week) in the program, to order and compare them.
    function ordinal(cycle, week) {
        return (cycle - 1) * weeks() + (week - 1);
    }

    function targetRpe(slot, week) {
        const targets = slot.rpe_targets;
        if (!targets || !targets.length) return null;
        return targets.length === 1 ? targets[0] : targets[week - 1];
    }

    // --- Program data ---

    async function loadProgram() {
        try {
            const response = await fetch('/api/program', { headers: { Accept: 'application/json' } });
            if (response.ok) {
                data = await response.json();
            }
        } catch (e) {
            // Offline with nothing cached yet: keep whatever we had.
        }
        if (data) mergeUnsynced();
        document.dispatchEvent(new CustomEvent('program-data')); // static/program.js re-renders the editor
    }

    function sessionFromPayload(payload) {
        return {
            client_id: payload.client_id,
            day_id: payload.day_id,
            day_name: payload.day_name,
            cycle: payload.cycle,
            week: payload.week,
            date: payload.date,
            entries: payload.entries.map((e) => {
                const info = data.movements[e.movement];
                return { ...e, date: payload.date, bodyweight: info && info.dip_belt ? data.bodyweight : null };
            }),
        };
    }

    // Make sessions that haven't reached the server yet visible to suggestions and history.
    function mergeUnsynced() {
        const unsynced = load(UNSYNCED_KEY, {});
        for (const payload of Object.values(unsynced)) {
            upsertLocalSession(sessionFromPayload(payload));
        }
    }

    function upsertLocalSession(session) {
        if (!data) return;
        const index = data.sessions.findIndex((s) => s.client_id === session.client_id);
        if (index >= 0) data.sessions[index] = session;
        else data.sessions.push(session);
    }

    // Cycle and week for a workout day. The cycle is never chosen by hand: it's the current
    // one (the furthest reached so far) until this day has been done in its last week, and
    // then the next one starts. The week is the furthest week reached, or the week after it
    // if this day was already done in that week.
    function suggestCycleWeek(dayId) {
        const sessions = data.sessions;
        if (!sessions.length) return { cycle: 1, week: 1, isNew: true };
        const latest = sessions.reduce((a, b) => (ordinal(b.cycle, b.week) > ordinal(a.cycle, a.week) ? b : a));
        const doneAlready = sessions.some(
            (s) => s.day_id === dayId && s.cycle === latest.cycle && s.week === latest.week
        );
        if (!doneAlready) return { cycle: latest.cycle, week: latest.week, isNew: false };
        return latest.week >= weeks()
            ? { cycle: latest.cycle + 1, week: 1, isNew: true }
            : { cycle: latest.cycle, week: latest.week + 1, isNew: false };
    }

    // The session to take loads from: the previous week of this cycle, or week 1 of
    // the previous cycle when starting a new one. Falls back to the most recent earlier
    // session of the same day (e.g. if a week was skipped).
    function findReferenceSession(dayId, cycle, week, excludeClientId) {
        const candidates = data.sessions
            .filter((s) => s.day_id === dayId && s.client_id !== excludeClientId)
            .sort((a, b) => dayOf(b.date).localeCompare(dayOf(a.date)));
        const wanted = week > 1 ? { cycle, week: week - 1 } : { cycle: cycle - 1, week: 1 };
        return (
            candidates.find((s) => s.cycle === wanted.cycle && s.week === wanted.week) ||
            candidates.find((s) => ordinal(s.cycle, s.week) < ordinal(cycle, week)) ||
            null
        );
    }

    // Most recent time a movement was logged anywhere (inside or outside sessions).
    function latestEntryFor(movement, excludeClientId) {
        const entries = [...(data.recent_by_movement[movement] || [])];
        for (const s of data.sessions) {
            if (s.client_id === excludeClientId) continue;
            for (const e of s.entries) {
                if (e.movement === movement) entries.push({ ...e, date: s.date });
            }
        }
        entries.sort((a, b) => dayOf(b.date).localeCompare(dayOf(a.date)));
        return entries[0] || null;
    }

    // Weight for a target reps/RPE using Brzycki's formula, as the Weight Recommendation does
    // (best estimated 1RM of recent exercises with an RPE).
    function suggestWeight(movement, reps, rpe, extraEntries) {
        const candidates = [...(data.recent_by_movement[movement] || []), ...extraEntries]
            .filter((e) => e.movement === movement && e.rpe !== null && e.rpe !== undefined);
        if (!candidates.length) return null;
        const oneRepMax = Math.max(...candidates.map((e) => {
            const bw = e.bodyweight || 0;
            return (e.weight + bw) * 36 / (37 - (e.reps + (10 - e.rpe))) - bw;
        }));
        const info = data.movements[movement];
        const bw = info && info.dip_belt ? data.bodyweight : 0;
        const total = (oneRepMax + bw) * (37 - (reps + (10 - rpe))) / 36;
        const plate = data.lightest_plate;
        return Math.round((total - bw) / plate) * plate;
    }

    // Everything the step screen shows about the current exercise.
    function stepInfo(movement) {
        const slot = active.slots[active.index];
        const reference = active.reference_client_id
            ? data.sessions.find((s) => s.client_id === active.reference_client_id)
            : null;
        let refEntry = null;
        let refLabel = '';
        if (reference) {
            refEntry = reference.entries.find((e) => e.movement === movement) || null;
            const weekLabel = reference.cycle === active.cycle
                ? `Week ${reference.week}` : `Cycle ${reference.cycle}, week ${reference.week}`;
            refLabel = `${weekLabel} · ${formatShortDay(reference.date)}`;
        }
        if (!refEntry) {
            refEntry = latestEntryFor(movement, active.client_id);
            refLabel = refEntry ? `Logged ${formatShortDay(refEntry.date)}` : '';
        }
        const rpe = targetRpe(slot, active.week);
        const suggested = rpe === null
            ? null
            : suggestWeight(movement, slot.reps, rpe, reference ? reference.entries : []);
        return { slot, rpe, refEntry, refLabel, suggested };
    }

    function defaultMovement(slot) {
        if (slot.movements.length === 1) return slot.movements[0];
        const reference = active.reference_client_id
            ? data.sessions.find((s) => s.client_id === active.reference_client_id)
            : null;
        const used = reference && reference.entries.find((e) => slot.movements.includes(e.movement));
        return used ? used.movement : slot.movements[0];
    }

    // --- Saving ---

    function sessionPayload() {
        return {
            client_id: active.client_id,
            day_id: active.day_id,
            day_name: active.day_name,
            cycle: active.cycle,
            week: active.week,
            date: active.date,
            entries: active.entries.filter(Boolean),
        };
    }

    function saveActive() {
        store(ACTIVE_KEY, active);
        if (!active) return;
        const payload = sessionPayload();
        const unsynced = load(UNSYNCED_KEY, {});
        unsynced[payload.client_id] = payload;
        store(UNSYNCED_KEY, unsynced);
        upsertLocalSession(sessionFromPayload(payload));
        syncSessions();
    }

    function setStatus(text, kind) {
        status = { text, kind };
        const el = document.getElementById('wz-status');
        if (el) {
            el.textContent = text;
            el.className = `wizard-status ${kind}`;
        }
    }

    // Uploads run one after another so an older copy of a session can never overwrite a newer one.
    let syncChain = Promise.resolve();
    function syncSessions() {
        syncChain = syncChain.then(uploadUnsynced, uploadUnsynced);
        return syncChain;
    }

    async function uploadUnsynced() {
        const pending = load(UNSYNCED_KEY, {});
        let uploaded = false;
        for (const [clientId, payload] of Object.entries(pending)) {
            let ok = false;
            try {
                const response = await fetch('/api/workout-sessions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify(payload),
                });
                const body = await response.json().catch(() => null);
                ok = response.ok && body && body.status === 'ok';
                if (!ok && response.status >= 400 && response.status < 500) {
                    // The server will never accept this one (e.g. its workout day was deleted).
                    dropUnsynced(clientId, payload);
                    setStatus(`Couldn't save the ${payload.day_name} session: ${body && body.detail ? JSON.stringify(body.detail) : response.status}`, 'error');
                    continue;
                }
            } catch (e) {
                ok = false;
            }
            if (!ok) {
                setStatus('Saved on this device. It will upload when you\'re back online.', 'offline');
                return;
            }
            dropUnsynced(clientId, payload);
            uploaded = true;
        }
        if (uploaded) {
            setStatus('Saved', 'ok');
            document.body.dispatchEvent(new Event('exercise-logged'));
            renderHistory();
        }
    }

    // Remove an uploaded session from the queue, unless it changed while the upload was running.
    function dropUnsynced(clientId, payload) {
        const current = load(UNSYNCED_KEY, {});
        if (JSON.stringify(current[clientId]) === JSON.stringify(payload)) {
            delete current[clientId];
            store(UNSYNCED_KEY, current);
        }
    }

    async function deleteSession(clientId) {
        // Take it out of the upload queue first and let any upload in flight finish,
        // so the session can't be re-created on the server right after being deleted.
        const unsynced = load(UNSYNCED_KEY, {});
        const queued = unsynced[clientId];
        delete unsynced[clientId];
        store(UNSYNCED_KEY, unsynced);
        await syncChain;
        try {
            const response = await fetch(`/api/workout-sessions/${encodeURIComponent(clientId)}/delete`, { method: 'POST' });
            // 404: it never reached the server, which is fine.
            if (!response.ok && response.status !== 404) throw new Error(String(response.status));
        } catch (e) {
            if (queued) {
                const current = load(UNSYNCED_KEY, {});
                current[clientId] = queued;
                store(UNSYNCED_KEY, current);
            }
            alert('Could not delete the session. Are you online?');
            return false;
        }
        if (data) data.sessions = data.sessions.filter((s) => s.client_id !== clientId);
        document.body.dispatchEvent(new Event('exercise-logged'));
        renderHistory();
        return true;
    }

    // --- Rendering: wizard ---

    const WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

    function render() {
        const root = document.getElementById('workout-app');
        if (!root) return;
        if (!data) {
            root.innerHTML = '<p class="muted">Couldn\'t load your program. Connect to the internet and reload.</p>';
            return;
        }
        const screen = !active ? 'start' : active.paused ? 'paused' : active.finished ? 'finished' : `step-${active.index}`;
        if (screen === 'finished') renderFinished(root);
        else if (screen === 'paused') renderPaused(root);
        else if (active) renderStep(root);
        else renderStart(root);

        // Focus mode: while a session is running, the tab bar gives way to a bar with Back/Skip/Next.
        document.body.classList.toggle('in-session', Boolean(active) && !active.paused);

        // Slide to a new screen (not when re-rendering the same one, e.g. after picking a movement variant).
        if (screen !== lastScreen) {
            if (lastScreen !== null) window.scrollTo({ top: 0 }); // Each screen starts at its top
            root.dataset.dir = navDirection;
            root.classList.remove('wz-enter');
            void root.offsetWidth; // Restart the animation
            root.classList.add('wz-enter');
        }
        lastScreen = screen;
        navDirection = 'forward';
        // The progress segment fill animates once, when the screen first appears. Redraws of the same
        // screen (e.g. picking another movement variant) must not replay it.
        if (active) active.just_done = null;
    }
    let lastScreen = null;
    let navDirection = 'forward';

    const PAUSE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M9 6v12M15 6v12"/></svg>';
    const CHECK_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

    function sessionTop(right) {
        return `
            <div class="wz-top">
                <div class="wz-top-title">
                    <span class="wz-day">${esc(active.day_name)}</span>
                    <span class="chip neutral">Week ${active.week}</span>
                </div>
                <div class="wz-top-right">${right}</div>
            </div>`;
    }

    // Paused: the session stays saved; the tab bar comes back so other pages can be used.
    function renderPaused(root) {
        const done = active.entries.filter(Boolean).length;
        root.innerHTML = `
            <div class="wz-eyebrow">Session paused</div>
            <h2 class="wz-title">${esc(active.day_name)}<span class="wz-subtitle">Week ${active.week} · exercise ${active.index + 1} of ${active.slots.length} · ${done} logged</span></h2>
            <div class="wz-progress">${progressSegments()}</div>
            <button type="button" class="wz-primary" id="wz-resume">Resume</button>
            <div class="wz-footer"><button type="button" class="link-button" id="wz-cancel">Cancel session</button></div>
        `;
        root.querySelector('#wz-resume').addEventListener('click', () => {
            active.paused = false;
            store(ACTIVE_KEY, active);
            render();
        });
        root.querySelector('#wz-cancel').addEventListener('click', cancelSession);
    }

    async function cancelSession() {
        const anySaved = active.entries.some(Boolean);
        if (!confirm(anySaved ? 'Cancel this session and delete what you logged in it?' : 'Cancel this session?')) return;
        if (anySaved && !(await deleteSession(active.client_id))) return;
        active = null;
        store(ACTIVE_KEY, null);
        setStatus('', '');
        render();
    }

    function targetLabel(slot, week) {
        const rpe = targetRpe(slot, week);
        return `${slot.sets} × ${slot.reps}${rpe !== null ? ` @ ${fmt(rpe)}` : ''}`;
    }

    function renderStart(root) {
        const now = new Date();
        const todayWeekday = (now.getDay() + 6) % 7; // 0 = Monday
        const eyebrow = `${WEEKDAY_NAMES[todayWeekday]}, ${formatDay(todayISO())}`;
        if (!data.days.length) {
            root.innerHTML = `
                <div class="wz-eyebrow">${eyebrow}</div>
                <h2 class="wz-title">Set up your program</h2>
                <p class="muted">Add your workout days (e.g. Primary Pull) with their exercises, and they'll show up here.</p>
                <a href="#program" role="button" class="wz-primary">Go to Program</a>
            `;
            return;
        }
        const todays = data.days.find((d) => d.weekday === todayWeekday);
        let selectedId = (todays || data.days[0]).id;
        const dayButtons = data.days.map((d) => {
            const meta = [
                d.weekday !== null && d.weekday !== undefined ? WEEKDAY_NAMES[d.weekday] : null,
                `${d.slots.length} exercise${d.slots.length === 1 ? '' : 's'}`,
            ].filter(Boolean).join(' · ');
            return `
                <button type="button" class="day-option" data-day="${d.id}">
                    <span><span class="day-name">${esc(d.name)}</span><span class="day-meta">${meta}</span></span>
                    ${todays && d.id === todays.id ? '<span class="chip">Today</span>' : ''}
                </button>`;
        }).join('');

        root.innerHTML = `
            <form id="wz-start">
                <h2 class="wz-title">${WEEKDAY_NAMES[todayWeekday]}<span class="wz-subtitle">${formatDay(todayISO())}</span></h2>
                <div class="day-picker">${dayButtons}</div>
                <div class="week-block">
                    <div class="week-heading" id="wz-cycle-label"></div>
                    <div class="week-strip" id="wz-weeks" role="radiogroup" aria-label="Week of the cycle"></div>
                </div>
                <label class="wz-date-label">Date ${window.dateFieldHtml('wz-date', todayISO())}</label>
                <ol id="wz-preview" class="wz-preview"></ol>
                <button type="submit" class="wz-primary" id="wz-start-button">Start</button>
            </form>
        `;

        let cycle = 1;
        let isNewCycle = false;
        let week = 1;
        const selectedDay = () => data.days.find((d) => d.id === selectedId);

        // One segment per week: done (this day already logged that week of the cycle), skipped
        // (the program has moved past that week without this day being logged), selected, or upcoming.
        const renderWeeks = () => {
            const done = new Set(data.sessions
                .filter((s) => s.day_id === selectedId && s.cycle === cycle)
                .map((s) => s.week));
            const furthest = Math.max(0, ...data.sessions.filter((s) => s.cycle === cycle).map((s) => s.week));
            const skipped = (w) => !done.has(w) && w < furthest;
            root.querySelector('#wz-cycle-label').innerHTML =
                `Cycle ${cycle}${isNewCycle ? ' <span class="chip">new</span>' : ''} <span class="muted">· Week ${week} of ${weeks()}</span>`;
            root.querySelector('#wz-weeks').innerHTML = Array.from({ length: weeks() }, (_, i) => {
                const w = i + 1;
                const state = w === week ? 'selected' : done.has(w) ? 'done' : skipped(w) ? 'skipped' : '';
                const sub = w === week
                    ? (done.has(w) ? 'again' : 'now')
                    : done.has(w) ? 'done' : skipped(w) ? 'skipped' : '';
                return `
                    <button type="button" role="radio" class="week-seg ${state}" data-week="${w}"
                            aria-checked="${w === week}" aria-label="Week ${w}${sub && w !== week ? `, ${sub}` : ''}">
                        <span class="week-num">${done.has(w) && w !== week ? '✓' : w}</span>
                        <span class="week-sub">${sub}</span>
                    </button>`;
            }).join('');
            root.querySelectorAll('.week-seg').forEach((button) => {
                button.addEventListener('click', () => {
                    week = parseInt(button.dataset.week, 10);
                    renderWeeks();
                    updatePreview();
                });
            });
        };

        const updatePreview = () => {
            const day = selectedDay();
            root.querySelector('#wz-preview').innerHTML = day.slots.map((slot) => `
                <li>
                    <span>${esc(slot.movements[0])}${slot.movements.length > 1
                        ? `<br><span class="alt">or ${slot.movements.slice(1).map(esc).join(', ')}</span>` : ''}</span>
                    <span class="chip">${targetLabel(slot, week)}</span>
                </li>`).join('');
            root.querySelector('#wz-start-button').textContent = `Start ${day.name}`;
        };
        const selectDay = (id) => {
            selectedId = id;
            root.querySelectorAll('.day-option').forEach((button) => {
                button.setAttribute('aria-pressed', String(parseInt(button.dataset.day, 10) === id));
            });
            const suggestion = suggestCycleWeek(id);
            cycle = suggestion.cycle;
            isNewCycle = suggestion.isNew && data.sessions.length > 0;
            week = suggestion.week;
            renderWeeks();
            updatePreview();
        };
        root.querySelectorAll('.day-option').forEach((button) => {
            button.addEventListener('click', () => selectDay(parseInt(button.dataset.day, 10)));
        });
        selectDay(selectedId);

        root.querySelector('#wz-start').addEventListener('submit', (event) => {
            event.preventDefault();
            const day = selectedDay();
            const clientId = newClientId();
            const reference = findReferenceSession(day.id, cycle, week, clientId);
            active = {
                client_id: clientId,
                day_id: day.id,
                day_name: day.name,
                slots: JSON.parse(JSON.stringify(day.slots)), // Snapshot, in case the program is edited mid-session
                cycle,
                week,
                date: root.querySelector('#wz-date').value || todayISO(),
                reference_client_id: reference ? reference.client_id : null,
                index: 0,
                entries: day.slots.map(() => null),
                finished: false,
            };
            store(ACTIVE_KEY, active); // Nothing to upload until the first exercise is done
            setStatus('', '');
            render();
        });
    }

    function stepper(id, label, value, { step, min, max, inputmode, required, placeholder, startAt }) {
        const name = label.replace(/<[^>]*>/g, '').trim(); // Plain text for aria-labels
        return `
            <div class="stepper">
                <label for="${id}">${label}</label>
                <div class="stepper-control">
                    <button type="button" data-target="${id}" data-step="${-step}" aria-label="Decrease ${name}">−</button>
                    <input type="${inputmode === 'decimal' ? 'text' : 'number'}" id="${id}" inputmode="${inputmode}" autocomplete="off"
                        ${min !== undefined ? `data-min="${min}"` : ''} ${max !== undefined ? `data-max="${max}"` : ''}
                        ${required ? 'required' : ''} ${placeholder ? `placeholder="${placeholder}"` : ''}
                        data-start-at="${startAt ?? ''}" value="${value ?? ''}">
                    <button type="button" data-target="${id}" data-step="${step}" aria-label="Increase ${name}">+</button>
                </div>
            </div>`;
    }

    function progressSegments() {
        return active.slots.map((_, i) => {
            let state = '';
            if (i === active.index && !active.finished) state = 'current';
            else if (active.entries[i]) state = 'done';
            else if (i < active.index || active.finished) state = 'skipped';
            if (i === active.just_done) state += ' just-done';
            return `<span class="${state}"></span>`;
        }).join('');
    }

    function sessionEyebrow() {
        return `${esc(active.day_name)} · Cycle ${active.cycle} · Week ${active.week} · ${formatDay(active.date)}`;
    }

    function renderStep(root, chosenMovement) {
        const slot = active.slots[active.index];
        const existing = active.entries[active.index];
        const movement = chosenMovement || (existing ? existing.movement : defaultMovement(slot));
        const info = stepInfo(movement);
        const total = active.slots.length;

        // Prefill: what was entered before (when going back), else plan targets and suggested/previous load.
        const keepExisting = existing && existing.movement === movement;
        const values = keepExisting ? existing : {
            sets: slot.sets,
            reps: slot.reps,
            weight: info.suggested !== null ? info.suggested : (info.refEntry ? info.refEntry.weight : ''),
            rpe: info.rpe,
        };

        const movementHtml = slot.movements.length > 1
            ? `<select id="wz-movement" class="wz-movement-select" aria-label="Movement">${slot.movements.map((m) =>
                `<option value="${esc(m)}"${m === movement ? ' selected' : ''}>${esc(m)}</option>`).join('')}</select>`
            : `<h2 class="wz-movement">${esc(movement)}</h2>`;

        const chips = [`<span class="chip">${slot.sets} × ${slot.reps}</span>`];
        if (info.rpe !== null) chips.push(`<span class="chip">RPE ${fmt(info.rpe)}</span>`);

        const tiles = [];
        if (info.refEntry) {
            const sub = `${info.refEntry.sets} × ${info.refEntry.reps}${info.refEntry.rpe !== null && info.refEntry.rpe !== undefined ? ` · RPE ${fmt(info.refEntry.rpe)}` : ''}`;
            tiles.push(`
                <div class="tile">
                    <div class="tile-label">Last time</div>
                    <div class="tile-value">${fmt(info.refEntry.weight)} kg</div>
                    <div class="tile-sub">${sub}<br>${esc(info.refLabel)}</div>
                </div>`);
        } else {
            tiles.push(`
                <div class="tile">
                    <div class="tile-label">Last time</div>
                    <div class="tile-sub">No previous data for this movement</div>
                </div>`);
        }
        if (info.suggested !== null) {
            tiles.push(`
                <div class="tile accent">
                    <div class="tile-label">Suggested</div>
                    <div class="tile-value">${fmt(info.suggested)} kg</div>
                    <div class="tile-sub">for RPE ${fmt(info.rpe)}</div>
                </div>`);
        }
        const isLast = active.index === total - 1;

        root.innerHTML = `
            ${sessionTop(`<span class="wz-count">${active.index + 1}/${total}</span>
                <button type="button" class="icon-button" id="wz-pause" aria-label="Pause session" title="Pause">${PAUSE_ICON}</button>`)}
            <div class="wz-progress">${progressSegments()}</div>
            ${movementHtml}
            <div class="chips">${chips.join('')}</div>
            <div class="tiles">${tiles.join('')}</div>
            <form id="wz-form" novalidate>
                <div class="stepper-grid">
                    ${stepper('wz-sets', 'Sets', values.sets, { step: 1, min: 1, max: 10, inputmode: 'numeric', required: true })}
                    ${stepper('wz-reps', 'Reps', values.reps, { step: 1, min: 1, max: 20, inputmode: 'numeric', required: true })}
                    ${stepper('wz-weight', 'Load (kg)', values.weight, { step: data.lightest_plate, inputmode: 'decimal', required: true, startAt: 0 })}
                    ${stepper('wz-rpe', 'RPE <span class="muted">(opt.)</span>', values.rpe, { step: 0.5, min: 6, max: 10, inputmode: 'decimal', placeholder: '–', startAt: 8 })}
                </div>
                <div class="wz-action-bar">
                    <div class="wz-actions">
                        <button type="button" class="secondary outline" id="wz-back"${active.index === 0 ? ' disabled' : ''}>Back</button>
                        <button type="button" class="secondary outline" id="wz-skip">Skip</button>
                        <button type="submit">${isLast ? 'Finish' : 'Next'}</button>
                    </div>
                </div>
            </form>
            <p id="wz-status" class="wizard-status ${status.kind}">${esc(status.text)}</p>
            <div class="wz-footer"><button type="button" class="link-button" id="wz-cancel">Cancel session</button></div>
        `;

        root.querySelectorAll('.stepper-control button').forEach((button) => {
            button.addEventListener('click', () => {
                const input = root.querySelector(`#${button.dataset.target}`);
                const step = parseFloat(button.dataset.step);
                const start = input.dataset.startAt !== '' ? parseFloat(input.dataset.startAt) : 0;
                const current = parseFloat(input.value.replace(',', '.'));
                let value = Number.isNaN(current) ? start : current + step;
                if (input.dataset.min !== undefined) value = Math.max(parseFloat(input.dataset.min), value);
                if (input.dataset.max !== undefined) value = Math.min(parseFloat(input.dataset.max), value);
                input.value = String(Math.round(value * 100) / 100);
            });
        });
        const movementSelect = root.querySelector('#wz-movement');
        if (movementSelect) {
            movementSelect.addEventListener('change', () => renderStep(root, movementSelect.value));
        }
        root.querySelector('#wz-back').addEventListener('click', () => {
            active.index = Math.max(0, active.index - 1);
            active.just_done = null;
            store(ACTIVE_KEY, active);
            navDirection = 'back';
            render();
        });
        root.querySelector('#wz-pause').addEventListener('click', () => {
            active.paused = true;
            store(ACTIVE_KEY, active);
            render();
        });
        root.querySelector('#wz-skip').addEventListener('click', () => {
            active.entries[active.index] = null;
            advance();
        });
        root.querySelector('#wz-form').addEventListener('submit', (event) => {
            event.preventDefault();
            const form = event.target;
            const read = (id) => root.querySelector(`#${id}`).value.trim().replace(',', '.');
            const sets = parseInt(read('wz-sets'), 10);
            const reps = parseInt(read('wz-reps'), 10);
            const weight = parseFloat(read('wz-weight'));
            const rpe = read('wz-rpe') === '' ? null : parseFloat(read('wz-rpe'));
            const problems = [];
            if (!(sets >= 1 && sets <= 10)) problems.push('sets must be 1–10');
            if (!(reps >= 1 && reps <= 20)) problems.push('reps must be 1–20');
            if (Number.isNaN(weight)) problems.push('enter a load');
            if (rpe !== null && !(rpe >= 6 && rpe <= 10)) problems.push('RPE must be 6–10');
            if (problems.length) {
                setStatus(`Check the values: ${problems.join(', ')}.`, 'error');
                return;
            }
            active.entries[active.index] = { movement, sets, reps, weight, rpe };
            advance();
        });
        root.querySelector('#wz-cancel').addEventListener('click', cancelSession);
    }

    function advance() {
        if (status.kind === 'error') setStatus('', '');
        active.just_done = active.entries[active.index] ? active.index : null;
        active.index += 1;
        if (active.index >= active.slots.length) {
            active.index = active.slots.length - 1;
            active.finished = true;
        }
        saveActive();
        render();
    }

    function entryDetail(entry) {
        let text = `${entry.sets} × ${entry.reps} · ${fmt(entry.weight)} kg`;
        if (entry.rpe !== null && entry.rpe !== undefined) text += ` · RPE ${fmt(entry.rpe)}`;
        return text;
    }

    // Estimated 1RM (external, like the server's get_onerepmax), or null without an RPE.
    function estimatedOneRepMax(entry) {
        if (entry.rpe === null || entry.rpe === undefined) return null;
        const bw = entry.bodyweight || 0;
        return (entry.weight + bw) * 36 / (37 - (entry.reps + (10 - entry.rpe))) - bw;
    }

    // Best estimated 1RM for a movement before this session, or null if there's no earlier set with an RPE.
    function previousBest(movement) {
        const earlier = [...(data.recent_by_movement[movement] || []).filter((e) => e.session_client_id !== active.client_id)];
        for (const s of data.sessions) {
            if (s.client_id === active.client_id) continue;
            for (const e of s.entries) if (e.movement === movement) earlier.push(e);
        }
        const values = earlier.map(estimatedOneRepMax).filter((v) => v !== null);
        return values.length ? Math.max(...values) : null;
    }

    // How each exercise compares with the reference session, and whether it's a new best.
    function entryComparison(entry) {
        const reference = active.reference_client_id
            ? data.sessions.find((s) => s.client_id === active.reference_client_id) : null;
        const refEntry = reference && reference.entries.find((e) => e.movement === entry.movement);
        let delta = '';
        if (refEntry) {
            const diff = Math.round((entry.weight - refEntry.weight) * 100) / 100;
            const vs = reference.cycle === active.cycle ? `week ${reference.week}` : `cycle ${reference.cycle}`;
            delta = diff > 0 ? `<span class="delta up">+${fmt(diff)} kg vs ${vs}</span>`
                : diff < 0 ? `<span class="delta down">−${fmt(-diff)} kg vs ${vs}</span>`
                : `<span class="delta">Same load as ${vs}</span>`;
        }
        const info = data.movements[entry.movement];
        const estimate = estimatedOneRepMax({ ...entry, bodyweight: info && info.dip_belt ? data.bodyweight : null });
        const best = previousBest(entry.movement);
        const isBest = estimate !== null && best !== null && estimate > best + 0.05;
        return { delta, isBest, estimate };
    }

    function renderFinished(root) {
        const done = active.entries.filter(Boolean);
        const sets = done.reduce((sum, e) => sum + e.sets, 0);
        const items = active.slots.map((slot, i) => {
            const entry = active.entries[i];
            if (!entry) {
                return `<li class="skipped-item"><div class="name">${slot.movements.map(esc).join(' / ')}</div><div class="detail">Skipped</div></li>`;
            }
            const { delta, isBest, estimate } = entryComparison(entry);
            return `
                <li>
                    <div class="summary-row">
                        <div class="name">${esc(entry.movement)}</div>
                        ${isBest ? `<span class="chip best">New best · ${fmt(Math.round(estimate * 2) / 2)} kg 1RM</span>` : ''}
                    </div>
                    <div class="detail">${entryDetail(entry)}</div>
                    ${delta ? `<div class="detail">${delta}</div>` : ''}
                </li>`;
        }).join('');
        root.innerHTML = `
            <div class="wz-progress">${progressSegments()}</div>
            <div class="wz-done">
                <div class="wz-done-icon">${CHECK_ICON}</div>
                <h2>Session complete</h2>
                <div class="muted">${sessionEyebrow()}</div>
                <div class="wz-stats">
                    <div><strong>${done.length}</strong><span>of ${active.slots.length} exercises</span></div>
                    <div><strong>${sets}</strong><span>sets</span></div>
                </div>
            </div>
            <ul class="summary-list">${items}</ul>
            <p id="wz-status" class="wizard-status ${status.kind}">${esc(status.text)}</p>
            <div class="wz-action-bar">
                <div class="wz-actions" style="grid-template-columns: 1fr 2fr;">
                    <button type="button" class="secondary outline" id="wz-edit">Back</button>
                    <button type="button" id="wz-done">Done</button>
                </div>
            </div>
        `;
        root.querySelector('#wz-edit').addEventListener('click', () => {
            active.finished = false;
            active.just_done = null;
            store(ACTIVE_KEY, active);
            navDirection = 'back';
            render();
        });
        root.querySelector('#wz-done').addEventListener('click', () => {
            active = null;
            store(ACTIVE_KEY, null);
            setStatus('', '');
            render();
            renderHistory();
        });
    }

    // --- Rendering: history ---

    // Every workout day across the weeks of the current cycle.
    function renderCycleOverview() {
        const root = document.getElementById('cycle-overview');
        if (!root || !data) return;
        if (!data.days.length) {
            root.innerHTML = '<p class="muted">Set up your program to follow your cycle here.</p>';
            return;
        }
        const cycle = data.sessions.length ? Math.max(...data.sessions.map((s) => s.cycle)) : 1;
        const furthest = Math.max(0, ...data.sessions.filter((s) => s.cycle === cycle).map((s) => s.week));
        const weekNumbers = Array.from({ length: weeks() }, (_, i) => i + 1);
        const rows = data.days.map((day) => {
            const done = new Set(data.sessions.filter((s) => s.day_id === day.id && s.cycle === cycle).map((s) => s.week));
            const cells = weekNumbers.map((w) => {
                const state = done.has(w) ? 'done' : w < furthest ? 'skipped' : '';
                const label = done.has(w) ? `Week ${w}: done` : w < furthest ? `Week ${w}: skipped` : `Week ${w}: to do`;
                return `<span class="cycle-cell ${state}" title="${label}" aria-label="${label}">${done.has(w) ? '✓' : ''}</span>`;
            }).join('');
            return `<div class="cycle-row"><span class="cycle-day">${esc(day.name)}</span><span class="cycle-cells">${cells}</span></div>`;
        }).join('');
        root.innerHTML = `
            <div class="cycle-row cycle-header">
                <span class="cycle-day"><strong>Cycle ${cycle}</strong></span>
                <span class="cycle-cells">${weekNumbers.map((w) => `<span class="cycle-week">W${w}</span>`).join('')}</span>
            </div>
            ${rows}`;
    }

    // --- Movement trends (History) ---

    let trends = null;

    async function loadTrends() {
        try {
            const response = await fetch('/api/trends', { headers: { Accept: 'application/json' } });
            if (response.ok) trends = await response.json();
        } catch (e) {
            // Offline and never cached: keep what we have.
        }
        renderTrends();
    }

    function sparkline(points) {
        const width = 96, height = 30, pad = 3;
        const values = points.map((p) => p.value);
        const min = Math.min(...values), max = Math.max(...values);
        const x = (i) => points.length === 1 ? width / 2 : pad + (i * (width - 2 * pad)) / (points.length - 1);
        const y = (v) => max === min ? height / 2 : height - pad - ((v - min) * (height - 2 * pad)) / (max - min);
        const path = points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
        const last = points.length - 1;
        return `
            <svg class="sparkline" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true">
                ${points.length > 1 ? `<polyline points="${path}" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>` : ''}
                <circle cx="${x(last).toFixed(1)}" cy="${y(points[last].value).toFixed(1)}" r="3" fill="currentColor"/>
            </svg>`;
    }

    function renderTrends() {
        const root = document.getElementById('movement-trends');
        if (!root) return;
        if (!trends) {
            root.innerHTML = '<p class="muted">Trends load when you\'re online.</p>';
            return;
        }
        if (!trends.trends.length) {
            root.innerHTML = '<p class="muted">Movements you log show up here with their trend.</p>';
            return;
        }
        root.innerHTML = trends.trends.map((t) => {
            const first = t.points[0].value;
            const last = t.points[t.points.length - 1].value;
            const diff = Math.round((last - first) * 2) / 2;
            const change = t.points.length < 2 ? ''
                : diff > 0 ? `<span class="delta up">+${fmt(diff)}</span>`
                : diff < 0 ? `<span class="delta down">−${fmt(-diff)}</span>`
                : '<span class="delta">±0</span>';
            return `
                <button type="button" class="trend-row" data-movement="${esc(t.movement)}">
                    <span class="trend-name">${esc(t.movement)}<small>${t.metric === 'e1rm' ? 'Est. 1RM' : 'Top load'}</small></span>
                    ${sparkline(t.points)}
                    <span class="trend-value">${fmt(Math.round(last * 2) / 2)} kg${change}</span>
                </button>`;
        }).join('') + `<p class="trend-note muted">Last ${Math.round(trends.days / 30)} months. Tap a movement for its chart.</p>`;

        root.querySelectorAll('.trend-row').forEach((row) => {
            row.addEventListener('click', () => {
                const select = document.getElementById('progress-exercise');
                if (!select || typeof loadProgress !== 'function') return;
                select.value = row.dataset.movement;
                loadProgress();
                document.getElementById('progress-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        });
    }

    function renderHistory() {
        renderCycleOverview();
        const root = document.getElementById('workout-history');
        if (!root) return;
        if (!data) {
            root.innerHTML = '<p class="muted">History is unavailable offline until the app has been opened online once.</p>';
            return;
        }
        const unsynced = load(UNSYNCED_KEY, {});
        const sessions = data.sessions
            .filter((s) => s.entries.length)
            .sort((a, b) => dayOf(b.date).localeCompare(dayOf(a.date)) || ordinal(b.cycle, b.week) - ordinal(a.cycle, a.week));
        if (!sessions.length) {
            root.innerHTML = '<p class="muted">No sessions logged yet. Finished workouts show up here.</p>';
            return;
        }
        const items = sessions.slice(0, historyShown).map((s) => {
            const [, m, d] = dayOf(s.date).split('-');
            return `
            <details class="history-item">
                <summary>
                    <span class="date-badge"><span class="day">${parseInt(d, 10)}</span><span class="month">${MONTHS[parseInt(m, 10) - 1]}</span></span>
                    <span class="history-title">${esc(s.day_name)}
                        <small>Cycle ${s.cycle} · Week ${s.week} · ${s.entries.length} exercise${s.entries.length === 1 ? '' : 's'}${s.client_id in unsynced ? ' · not uploaded yet' : ''}</small>
                    </span>
                </summary>
                <ul class="summary-list">
                    ${s.entries.map((e) => `<li><div class="name">${esc(e.movement)}</div><div class="detail">${entryDetail(e)}</div></li>`).join('')}
                </ul>
                <button type="button" class="secondary outline small-button" data-delete-session="${esc(s.client_id)}">Delete session</button>
            </details>`;
        }).join('');
        const more = sessions.length > historyShown
            ? '<button type="button" class="secondary outline small-button" id="history-more">Show more</button>'
            : '';
        root.innerHTML = items + more;

        root.querySelectorAll('[data-delete-session]').forEach((button) => {
            button.addEventListener('click', async () => {
                const clientId = button.dataset.deleteSession;
                if (!confirm('Delete this session?')) return;
                if (await deleteSession(clientId) && active && active.client_id === clientId) {
                    active = null;
                    store(ACTIVE_KEY, null);
                    render();
                }
            });
        });
        const moreButton = root.querySelector('#history-more');
        if (moreButton) {
            moreButton.addEventListener('click', () => {
                historyShown += HISTORY_PAGE;
                renderHistory();
            });
        }
    }

    // --- Startup ---

    async function refresh() {
        await loadProgram();
        // Keep the step screen as it is while a session is in progress, so typed values aren't lost.
        if (!active || !document.getElementById('wz-form')) render();
        renderHistory();
    }

    document.addEventListener('DOMContentLoaded', async () => {
        active = load(ACTIVE_KEY, null);
        await refresh();
        syncSessions();
        loadTrends();
    });
    // New exercises (session uploads, Log exercise) change the trends.
    document.body.addEventListener('exercise-logged', loadTrends);
    window.addEventListener('online', syncSessions);
    // A movement created on the Program tab (HX-Trigger) must show up in the movement pickers.
    document.body.addEventListener('movement-created', refresh);

    // Shared with static/program.js (the program editor).
    window.WorkoutApp = { getData: () => data, refresh, esc, fmt };
})();
