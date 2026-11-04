// Program editor (Program tab): workout days as cards, and a structured editor with one card
// per exercise. Movements are chosen from a searchable sheet instead of typed, so names can't
// be misspelled. Program data comes from static/workout.js (window.WorkoutApp); saving goes
// to /api/workout-days.

(() => {
    const WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const ICONS = {
        up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 15l6-6 6 6"/></svg>',
        down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>',
        remove: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
        close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
        chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>',
    };

    const app = () => window.WorkoutApp;
    const data = () => (app() ? app().getData() : null);
    const esc = (value) => app().esc(value);
    const fmt = (value) => app().fmt(value);
    const weeks = () => (data() ? data().cycle_weeks : 4);

    let editing = null; // Workout day being edited: { id, name, weekday, slots: [...] }
    let dirty = false;
    let message = null; // { text, kind: 'ok' | 'error' } shown above the list or in the editor

    // --- Editor state <-> stored slots ---

    function toEditable(day) {
        return {
            id: day.id,
            name: day.name,
            weekday: day.weekday,
            slots: day.slots.map((slot) => {
                const targets = slot.rpe_targets || [];
                return {
                    movements: [...slot.movements],
                    sets: slot.sets,
                    reps: slot.reps,
                    rpeMode: targets.length ? 'week' : 'none',
                    // Days saved before RPE was always per week may have one target for every week.
                    rpeWeeks: Array.from({ length: weeks() }, (_, i) =>
                        targets.length ? String(targets.length === 1 ? targets[0] : targets[i] ?? '') : ''),
                };
            }),
        };
    }

    function newSlot() {
        return { movements: [''], sets: 3, reps: 10, rpeMode: 'none', rpeWeeks: Array(weeks()).fill('') };
    }

    // Returns [payload, problem]: problem is a message for the first thing that needs fixing.
    function toPayload() {
        const name = editing.name.trim();
        if (!name) return [null, 'Give the workout day a name.'];
        if (!editing.slots.length) return [null, 'Add at least one exercise.'];
        const slots = [];
        for (const [i, slot] of editing.slots.entries()) {
            const label = `Exercise ${i + 1}`;
            const movements = slot.movements.filter(Boolean);
            if (!movements.length) return [null, `${label}: choose a movement.`];
            let rpeTargets = null;
            const parse = (text) => Number(String(text).trim().replace(',', '.'));
            const valid = (n) => String(n) !== '' && Number.isFinite(n) && n >= 6 && n <= 10;
            if (slot.rpeMode === 'week') {
                const values = slot.rpeWeeks.map(parse);
                if (slot.rpeWeeks.some((text) => text.trim() === '') || !values.every(valid)) {
                    return [null, `${label}: enter an RPE between 6 and 10 for every week.`];
                }
                rpeTargets = values;
            }
            slots.push({ movements, sets: slot.sets, reps: slot.reps, rpe_targets: rpeTargets });
        }
        return [{ name, weekday: editing.weekday, slots }, null];
    }

    // --- Rendering ---

    function render() {
        const root = document.getElementById('program-editor');
        if (!root) return;
        if (!data()) {
            root.innerHTML = '<p class="muted">Couldn\'t load your program. Connect to the internet and reload.</p>';
            return;
        }
        if (editing) renderEditor(root);
        else renderList(root);
    }

    function messageHtml() {
        if (!message) return '';
        return `<div class="${message.kind === 'ok' ? 'success-message' : 'failure-message'}" role="status">${esc(message.text)}</div>`;
    }

    function renderList(root) {
        const days = data().days;
        const cards = days.map((day) => {
            const names = day.slots.map((slot) => slot.movements[0]).join(', ');
            const meta = [day.weekday !== null && day.weekday !== undefined ? WEEKDAY_NAMES[day.weekday] : 'Any day',
                `${day.slots.length} exercise${day.slots.length === 1 ? '' : 's'}`].join(' · ');
            return `
                <button type="button" class="day-card" data-edit="${day.id}">
                    <span class="day-card-text">
                        <span class="day-card-name">${esc(day.name)}</span>
                        <span class="day-card-meta">${meta}</span>
                        <span class="day-card-exercises">${esc(names)}</span>
                    </span>
                    ${ICONS.chevron}
                </button>`;
        }).join('');
        root.innerHTML = `
            ${messageHtml()}
            <div class="day-cards">${cards || '<p class="muted">No workout days yet. Add your first one, e.g. Primary Pull.</p>'}</div>
            <button type="button" class="add-button" id="program-add">+ Add workout day</button>
        `;
        root.querySelectorAll('[data-edit]').forEach((button) => {
            button.addEventListener('click', () => {
                const day = data().days.find((d) => d.id === parseInt(button.dataset.edit, 10));
                startEditing(toEditable(day));
            });
        });
        root.querySelector('#program-add').addEventListener('click', () => {
            startEditing({ id: null, name: '', weekday: null, slots: [newSlot()] });
        });
    }

    function startEditing(state, note) {
        editing = state;
        dirty = false;
        message = note || null;
        render();
        window.scrollTo({ top: 0 });
    }

    function stepperHtml(id, label, value, min, max) {
        return `
            <div class="stepper">
                <label for="${id}">${label}</label>
                <div class="stepper-control">
                    <button type="button" data-step="-1" data-target="${id}" aria-label="Decrease ${label}">−</button>
                    <input type="number" id="${id}" inputmode="numeric" min="${min}" max="${max}" value="${value}">
                    <button type="button" data-step="1" data-target="${id}" aria-label="Increase ${label}">+</button>
                </div>
            </div>`;
    }

    function slotHtml(slot, i, count) {
        const movements = slot.movements.map((name, j) => `
            <div class="ex-movement">
                ${j > 0 ? '<span class="ex-or">or</span>' : ''}
                <button type="button" class="movement-pick${name ? '' : ' empty'}" data-pick="${i}:${j}">
                    <span>${name ? esc(name) : 'Choose movement'}</span>${ICONS.chevron}
                </button>
                ${j > 0 ? `<button type="button" class="icon-button small" data-remove-alt="${i}:${j}" aria-label="Remove alternative">${ICONS.close}</button>` : ''}
            </div>`).join('');

        const modes = [['none', 'No RPE'], ['week', 'RPE per week']].map(([mode, label]) =>
            `<button type="button" role="radio" aria-checked="${slot.rpeMode === mode}" data-rpe-mode="${i}:${mode}">${label}</button>`).join('');
        let rpeInputs = '';
        if (slot.rpeMode === 'week') {
            rpeInputs = `<div class="rpe-weeks">${slot.rpeWeeks.map((value, w) => `
                <label class="rpe-field">W${w + 1}
                    <input type="text" inputmode="decimal" data-min="6" data-max="10" autocomplete="off" placeholder="${[7, 7.5, 8, 8.5][w] ?? 8}"
                           data-rpe-week="${i}:${w}" value="${esc(value)}">
                </label>`).join('')}</div>`;
        }

        return `
            <div class="ex-card">
                <div class="ex-head">
                    <span class="ex-num">Exercise ${i + 1}</span>
                    <span class="ex-tools">
                        <button type="button" class="icon-button small" data-move="${i}:-1" aria-label="Move up"${i === 0 ? ' disabled' : ''}>${ICONS.up}</button>
                        <button type="button" class="icon-button small" data-move="${i}:1" aria-label="Move down"${i === count - 1 ? ' disabled' : ''}>${ICONS.down}</button>
                        <button type="button" class="icon-button small danger" data-remove="${i}" aria-label="Remove exercise">${ICONS.remove}</button>
                    </span>
                </div>
                ${movements}
                <button type="button" class="link-button add-alt" data-add-alt="${i}">+ Add alternative</button>
                <div class="stepper-grid">
                    ${stepperHtml(`ex-${i}-sets`, 'Sets', slot.sets, 1, 10)}
                    ${stepperHtml(`ex-${i}-reps`, 'Reps', slot.reps, 1, 20)}
                </div>
                <div class="segmented rpe-modes" role="radiogroup" aria-label="RPE target">${modes}</div>
                ${rpeInputs}
            </div>`;
    }

    function renderEditor(root) {
        const weekdayOptions = ['<option value="">Any day</option>', ...WEEKDAY_NAMES.map((name, i) =>
            `<option value="${i}"${editing.weekday === i ? ' selected' : ''}>${name}</option>`)].join('');
        root.innerHTML = `
            <button type="button" class="back-link link-like" id="program-back">‹ Program</button>
            <h2 class="editor-title">${editing.id ? 'Edit workout day' : 'New workout day'}</h2>
            ${messageHtml()}
            <article class="editor-card">
                <label>Name
                    <input type="text" id="day-name" value="${esc(editing.name)}" placeholder="e.g. Primary Pull" autocomplete="off">
                </label>
                <label>Day of week
                    <select id="day-weekday">${weekdayOptions}</select>
                </label>
            </article>
            ${editing.slots.map((slot, i) => slotHtml(slot, i, editing.slots.length)).join('')}
            <button type="button" class="add-button" id="add-exercise">+ Add exercise</button>
            <div class="editor-actions">
                <button type="button" id="day-save" class="wz-primary">Save</button>
                <div class="editor-secondary">
                    <button type="button" class="secondary outline" id="day-cancel">Cancel</button>
                    ${editing.id ? '<button type="button" class="secondary outline" id="day-duplicate">Duplicate</button>' : ''}
                    ${editing.id ? '<button type="button" class="secondary outline danger" id="day-delete">Delete</button>' : ''}
                </div>
            </div>
        `;
        bindEditor(root);
    }

    // --- Editor events ---

    function bindEditor(root) {
        const changed = () => { dirty = true; };
        const at = (value) => value.split(':').map((part) => (Number.isNaN(Number(part)) ? part : Number(part)));

        root.querySelector('#program-back').addEventListener('click', cancelEditing);
        root.querySelector('#day-cancel').addEventListener('click', cancelEditing);
        root.querySelector('#day-name').addEventListener('input', (event) => { editing.name = event.target.value; changed(); });
        root.querySelector('#day-weekday').addEventListener('change', (event) => {
            editing.weekday = event.target.value === '' ? null : Number(event.target.value);
            changed();
        });

        root.querySelectorAll('[data-pick]').forEach((button) => {
            button.addEventListener('click', () => {
                const [i, j] = at(button.dataset.pick);
                openPicker(editing.slots[i].movements[j], (name) => {
                    editing.slots[i].movements[j] = name;
                    changed();
                    render();
                });
            });
        });
        root.querySelectorAll('[data-add-alt]').forEach((button) => {
            button.addEventListener('click', () => {
                const i = Number(button.dataset.addAlt);
                openPicker('', (name) => {
                    if (!editing.slots[i].movements.includes(name)) editing.slots[i].movements.push(name);
                    changed();
                    render();
                });
            });
        });
        root.querySelectorAll('[data-remove-alt]').forEach((button) => {
            button.addEventListener('click', () => {
                const [i, j] = at(button.dataset.removeAlt);
                editing.slots[i].movements.splice(j, 1);
                changed();
                render();
            });
        });
        root.querySelectorAll('[data-move]').forEach((button) => {
            button.addEventListener('click', () => {
                const [i, delta] = at(button.dataset.move);
                const slots = editing.slots;
                [slots[i], slots[i + delta]] = [slots[i + delta], slots[i]];
                changed();
                render();
            });
        });
        root.querySelectorAll('[data-remove]').forEach((button) => {
            button.addEventListener('click', () => {
                const i = Number(button.dataset.remove);
                const name = editing.slots[i].movements[0];
                if (name && !confirm(`Remove ${name}?`)) return;
                editing.slots.splice(i, 1);
                changed();
                render();
            });
        });
        root.querySelectorAll('[data-rpe-mode]').forEach((button) => {
            button.addEventListener('click', () => {
                const [i, mode] = at(button.dataset.rpeMode);
                editing.slots[i].rpeMode = mode; // Typed values are kept if you switch back
                changed();
                render();
            });
        });
        root.querySelectorAll('[data-rpe-week]').forEach((input) => {
            input.addEventListener('input', () => {
                const [i, w] = at(input.dataset.rpeWeek);
                editing.slots[i].rpeWeeks[w] = input.value;
                changed();
            });
        });

        // Sets and reps steppers
        const setCount = (input, value) => {
            const clamped = Math.min(Number(input.max), Math.max(Number(input.min), Math.round(value)));
            input.value = String(clamped);
            const [, i, field] = input.id.split('-');
            editing.slots[Number(i)][field] = clamped;
            changed();
        };
        root.querySelectorAll('.stepper-control button').forEach((button) => {
            button.addEventListener('click', () => {
                const input = root.querySelector(`#${button.dataset.target}`);
                setCount(input, (Number(input.value) || 0) + Number(button.dataset.step));
            });
        });
        root.querySelectorAll('.stepper-control input').forEach((input) => {
            input.addEventListener('change', () => setCount(input, Number(input.value) || Number(input.min)));
        });

        root.querySelector('#add-exercise').addEventListener('click', () => {
            editing.slots.push(newSlot());
            changed();
            render();
            const cards = root.querySelectorAll('.ex-card');
            cards[cards.length - 1].scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        root.querySelector('#day-save').addEventListener('click', saveDay);
        const duplicate = root.querySelector('#day-duplicate');
        if (duplicate) duplicate.addEventListener('click', duplicateDay);
        const remove = root.querySelector('#day-delete');
        if (remove) remove.addEventListener('click', deleteDay);
    }

    function cancelEditing() {
        if (dirty && !confirm('Discard your changes?')) return;
        editing = null;
        message = null;
        render();
        window.scrollTo({ top: 0 });
    }

    function errorText(body, status) {
        if (body && typeof body.detail === 'string') return body.detail;
        if (body && Array.isArray(body.detail) && body.detail.length) return body.detail[0].msg;
        return `Couldn't save (error ${status}).`;
    }

    async function send(url, payload) {
        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: payload === undefined ? undefined : JSON.stringify(payload),
            });
            const body = await response.json().catch(() => null);
            if (response.ok && body && body.status === 'ok') return [body, null];
            return [null, errorText(body, response.status)];
        } catch (e) {
            return [null, 'You\'re offline. Changes to your program need a connection.'];
        }
    }

    async function saveDay() {
        const [payload, problem] = toPayload();
        if (problem) {
            message = { text: problem, kind: 'error' };
            render();
            window.scrollTo({ top: 0, behavior: 'smooth' });
            return;
        }
        const button = document.getElementById('day-save');
        button.setAttribute('aria-busy', 'true');
        const [, error] = await send(editing.id ? `/api/workout-days/${editing.id}` : '/api/workout-days', payload);
        if (error) {
            message = { text: error, kind: 'error' };
            render();
            window.scrollTo({ top: 0, behavior: 'smooth' });
            return;
        }
        editing = null;
        dirty = false;
        message = { text: `Saved ${payload.name}`, kind: 'ok' };
        await app().refresh();
        render();
        window.scrollTo({ top: 0 });
    }

    function duplicateDay() {
        if (dirty && !confirm('Duplicate without saving your changes?')) return;
        const day = data().days.find((d) => d.id === editing.id);
        const copy = toEditable(day);
        copy.id = null;
        copy.name = `${day.name} (copy)`;
        startEditing(copy, { text: 'This is a copy. Rename it and save.', kind: 'ok' });
        dirty = true;
    }

    async function deleteDay() {
        if (!confirm(`Delete ${editing.name}? Sessions you already logged are kept.`)) return;
        const [, error] = await send(`/api/workout-days/${editing.id}/delete`);
        if (error) {
            message = { text: error, kind: 'error' };
            render();
            return;
        }
        const name = editing.name;
        editing = null;
        dirty = false;
        message = { text: `Deleted ${name}`, kind: 'ok' };
        await app().refresh();
        render();
    }

    // --- Movement picker (bottom sheet) ---

    let pickerCallback = null;

    function pickerElement() {
        let sheet = document.getElementById('movement-picker');
        if (sheet) return sheet;
        sheet = document.createElement('div');
        sheet.id = 'movement-picker';
        sheet.className = 'sheet-backdrop';
        sheet.hidden = true;
        sheet.innerHTML = `
            <div class="sheet" role="dialog" aria-modal="true" aria-labelledby="picker-title">
                <div class="sheet-head">
                    <strong id="picker-title">Choose movement</strong>
                    <button type="button" class="icon-button" id="picker-close" aria-label="Close">${ICONS.close}</button>
                </div>
                <input type="search" id="picker-search" placeholder="Search movements" autocomplete="off" autocapitalize="off" spellcheck="false">
                <ul class="sheet-list" id="picker-list"></ul>
                <div class="sheet-create" id="picker-create" hidden>
                    <label><input type="checkbox" role="switch" id="picker-dip-belt"> Dip belt (load is added to bodyweight)</label>
                    <button type="button" id="picker-create-button"></button>
                    <small class="picker-hint" id="picker-create-hint" aria-live="polite"></small>
                </div>
            </div>`;
        document.body.appendChild(sheet);
        sheet.addEventListener('click', (event) => { if (event.target === sheet) closePicker(); });
        sheet.querySelector('#picker-close').addEventListener('click', closePicker);
        sheet.querySelector('#picker-search').addEventListener('input', renderPickerList);
        sheet.querySelector('#picker-create-button').addEventListener('click', createMovement);
        document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !sheet.hidden) closePicker(); });
        return sheet;
    }

    function openPicker(current, onPick) {
        const sheet = pickerElement();
        pickerCallback = onPick;
        sheet.dataset.current = current || '';
        const search = sheet.querySelector('#picker-search');
        search.value = '';
        sheet.querySelector('#picker-dip-belt').checked = false;
        renderPickerList();
        sheet.hidden = false;
        document.body.classList.add('sheet-open');
        search.focus();
    }

    function closePicker() {
        const sheet = document.getElementById('movement-picker');
        if (sheet) sheet.hidden = true;
        document.body.classList.remove('sheet-open');
        pickerCallback = null;
    }

    function pick(name) {
        const callback = pickerCallback;
        closePicker();
        if (callback) callback(name);
    }

    function renderPickerList() {
        const sheet = pickerElement();
        const query = sheet.querySelector('#picker-search').value.trim();
        const folded = query.toLocaleLowerCase();
        // Ignore spaces and hyphens, so "pull up" and "pullup" find "Pull-up".
        const loose = (text) => text.toLocaleLowerCase().replace(/[\s-]+/g, '');
        const names = Object.keys(data().movements).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
        const matches = names.filter((name) => loose(name).includes(loose(query)));
        const current = sheet.dataset.current;
        const list = sheet.querySelector('#picker-list');
        list.innerHTML = matches.map((name) => `
            <li><button type="button" class="sheet-item${name === current ? ' current' : ''}" data-name="${esc(name)}">
                <span>${esc(name)}</span>${data().movements[name].dip_belt ? '<small>Dip belt</small>' : ''}
            </button></li>`).join('') || `<li class="sheet-empty muted">No movement matches “${esc(query)}”.</li>`;
        list.querySelectorAll('.sheet-item').forEach((button) => {
            button.addEventListener('click', () => pick(button.dataset.name));
        });

        // Admins can create a missing movement right here, under its standard name (see movement_names.py).
        const exact = names.some((name) => name.toLocaleLowerCase() === folded);
        const create = sheet.querySelector('#picker-create');
        create.hidden = !(data().is_admin && query && !exact);
        if (!create.hidden) previewNewName(query);
    }

    // Ask the server for the standard form of the name being typed ("pull up" -> "Pull-up").
    let previewTimer = null;
    let previewName = null;
    function previewNewName(query) {
        const sheet = pickerElement();
        const button = sheet.querySelector('#picker-create-button');
        const hint = sheet.querySelector('#picker-create-hint');
        previewName = null;
        button.disabled = true;
        button.textContent = `Create “${query}”`;
        hint.textContent = '';
        clearTimeout(previewTimer);
        previewTimer = setTimeout(async () => {
            try {
                const response = await fetch(`/api/movements/normalize?name=${encodeURIComponent(query)}`);
                const result = await response.json();
                if (sheet.querySelector('#picker-search').value.trim() !== query) return; // Typed on since
                if (result.error) {
                    hint.textContent = result.error;
                    hint.className = 'picker-hint error';
                    return;
                }
                previewName = result.name;
                button.textContent = `Create “${result.name}”`;
                button.disabled = false;
                hint.textContent = result.name !== query ? 'Names are written in a standard form.' : '';
                hint.className = 'picker-hint';
            } catch (e) {
                hint.textContent = 'Creating movements needs a connection.';
                hint.className = 'picker-hint error';
            }
        }, 250);
    }

    async function createMovement() {
        const sheet = pickerElement();
        const name = previewName;
        if (!name) return;
        const form = new FormData();
        form.append('name', name);
        if (sheet.querySelector('#picker-dip-belt').checked) form.append('dip_belt', 'true');
        try {
            await fetch('/movements', { method: 'POST', body: form });
        } catch (e) { /* Checked below */ }
        await app().refresh();
        if (data().movements[name]) {
            pick(name);
        } else {
            sheet.querySelector('#picker-create-button').textContent = `Couldn't create “${name}”. Are you online?`;
        }
    }

    // --- Startup ---

    // workout.js loads (and reloads) the program data; show it unless an edit is in progress.
    document.addEventListener('program-data', () => {
        if (!editing) render();
    });
})();
