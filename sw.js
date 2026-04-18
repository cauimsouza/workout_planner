const CACHE_NAME = 'workout-tracker-v1';
const STATIC_ASSETS = [
    '/',
    '/manifest.json',
    '/static/icon.png',
];
const CDN_ASSETS = [
    'https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css',
    'https://cdn.jsdelivr.net/npm/chart.js',
    'https://unpkg.com/htmx.org@1.9.10',
];

// Install: cache the app shell
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) =>
            cache.addAll([...STATIC_ASSETS, ...CDN_ASSETS])
        )
    );
    self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
        )
    );
    self.clients.claim();
});

// Fetch: network-first for app routes, cache-first for static assets
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // POST/PUT: handle offline writes
    if (event.request.method === 'POST' || event.request.method === 'PUT') {
        event.respondWith(
            event.request.clone().text().then((body) =>
                fetch(event.request).catch(() =>
                    handleOfflineWrite(url, event.request.method, body)
                )
            )
        );
        return;
    }

    // CDN assets: cache-first
    if (url.origin !== location.origin) {
        event.respondWith(
            caches.match(event.request).then((cached) => cached || fetch(event.request))
        );
        return;
    }

    // Static assets: cache-first
    if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json') {
        event.respondWith(
            caches.match(event.request).then((cached) => cached || fetch(event.request))
        );
        return;
    }

    // App shell (root page): network-first, fallback to cache
    if (url.pathname === '/') {
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                    return response;
                })
                .catch(() => caches.match(event.request))
        );
        return;
    }

    // HTMX data endpoints: network-first, offline fallback from IndexedDB
    const offlineRoutes = ['/movements', '/exercises', '/bodyweight'];
    if (offlineRoutes.some((route) => url.pathname === route)) {
        event.respondWith(
            fetch(event.request)
                .catch(() => generateOfflineResponse(url))
        );
        return;
    }
});

// --- IndexedDB helpers ---

function openDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open('workout-tracker', 2);
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains('movements')) {
                db.createObjectStore('movements', { keyPath: 'name' });
            }
            if (!db.objectStoreNames.contains('exercises')) {
                const store = db.createObjectStore('exercises', { keyPath: 'id' });
                store.createIndex('by_exercise', 'exercise_name');
                store.createIndex('by_created_at', 'created_at');
            }
            if (!db.objectStoreNames.contains('user')) {
                db.createObjectStore('user', { keyPath: 'key' });
            }
            if (!db.objectStoreNames.contains('pending')) {
                db.createObjectStore('pending', { keyPath: 'id', autoIncrement: true });
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

function getAll(db, storeName) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readonly');
        const store = tx.objectStore(storeName);
        const request = store.getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

function getByKey(db, storeName, key) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readonly');
        const store = tx.objectStore(storeName);
        const request = store.get(key);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

function htmlResponse(html) {
    return new Response(html, {
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });
}

function formatNumber(n) {
    return parseFloat(n.toFixed(10)).toString();
}

function formatDate(isoString) {
    const d = new Date(isoString);
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const day = String(d.getUTCDate()).padStart(2, '0');
    const mon = months[d.getUTCMonth()];
    if (d.getUTCFullYear() === new Date().getFullYear()) {
        return `${day} ${mon}`;
    }
    const year = String(d.getUTCFullYear()).slice(-2);
    return `${day} ${mon} ${year}`;
}

// --- Offline response generators ---

async function generateOfflineResponse(url) {
    try {
        const db = await openDB();

        if (url.pathname === '/movements') {
            return generateMovementsResponse(db);
        }
        if (url.pathname === '/exercises') {
            const offset = parseInt(url.searchParams.get('offset') || '0');
            const limit = parseInt(url.searchParams.get('limit') || '5');
            return generateExercisesResponse(db, offset, limit);
        }
        if (url.pathname === '/bodyweight') {
            return generateBodyweightResponse(db);
        }
    } catch (e) {
        return htmlResponse('<p>Offline data unavailable.</p>');
    }
}

async function generateMovementsResponse(db) {
    const movements = await getAll(db, 'movements');
    if (!movements.length) {
        return htmlResponse('<option value="">No movements available</option>');
    }
    const options = ['<option value="">Select a movement</option>'];
    for (const m of movements) {
        options.push(`<option value="${m.name}">${m.name}</option>`);
    }
    return htmlResponse(options.join('\n'));
}

async function generateExercisesResponse(db, offset, limit) {
    const exercises = await getAll(db, 'exercises');
    // Sort descending by created_at
    exercises.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

    const page = exercises.slice(offset, offset + limit);
    const hasNext = exercises.length > offset + limit;
    const hasPrev = offset > 0;

    const rows = page.map((e) => `
        <tr>
            <th scope="row">${formatDate(e.created_at)}</th>
            <td>${e.exercise_name}</td>
            <td>${e.reps}</td>
            <td>${formatNumber(e.weight)}</td>
            <td>${formatNumber(e.rpe)}</td>
        </tr>
    `).join('');

    const prevBtn = hasPrev
        ? `<button hx-get="/exercises?offset=${Math.max(offset - limit, 0)}&limit=${limit}" hx-target="#previous-exercises" hx-swap="outerHTML">Previous</button>`
        : '';
    const nextBtn = hasNext
        ? `<button hx-get="/exercises?offset=${offset + limit}&limit=${limit}" hx-target="#previous-exercises" hx-swap="outerHTML">Next</button>`
        : '';

    return htmlResponse(`
        <div id="previous-exercises">
            <div style="overflow-x: auto">
            <table>
                <thead>
                    <tr>
                        <th scope="col">Date</th>
                        <th scope="col">Movement</th>
                        <th scope="col">Reps</th>
                        <th scope="col">Weight</th>
                        <th scope="col">RPE</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
            </div>
            <div class="pagination">
                <div>${prevBtn}</div>
                <div class="pagination-next">${nextBtn}</div>
            </div>
        </div>
    `);
}

async function generateBodyweightResponse(db) {
    const record = await getByKey(db, 'user', 'bodyweight');
    const bw = record ? record.value : '\u2014';
    return htmlResponse(`
        <div id="bodyweight-display">
            <p style="font-size: 0.9rem; color: var(--pico-muted-color); margin-top: 0.5rem;">
                Current: <strong>${typeof bw === 'number' ? formatNumber(bw) : bw}</strong>
            </p>
        </div>
    `);
}

// --- Offline write handlers ---

function parseFormData(body) {
    const params = new URLSearchParams(body);
    const data = {};
    for (const [key, value] of params) {
        data[key] = value;
    }
    return data;
}

function putItem(db, storeName, item) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        store.put(item);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

function addItem(db, storeName, item) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        const request = store.add(item);
        request.onsuccess = () => resolve(request.result);
        tx.onerror = () => reject(tx.error);
    });
}

function queuePending(db, action) {
    return addItem(db, 'pending', action);
}

async function handleOfflineWrite(url, method, body) {
    try {
        const db = await openDB();
        const form = parseFormData(body);

        if (url.pathname === '/exercises/' && method === 'POST') {
            return handleOfflineCreateExercise(db, form);
        }
        if (url.pathname === '/bodyweight' && method === 'PUT') {
            return handleOfflineUpdateBodyweight(db, form);
        }
        if (url.pathname === '/movements' && method === 'POST') {
            return handleOfflineCreateMovement(db, form);
        }
        if (url.pathname === '/recommendations' && method === 'POST') {
            return handleOfflineRecommendation(db, form);
        }

        return htmlResponse('<p>This action is not available offline.</p>');
    } catch (e) {
        return htmlResponse('<p>Offline write failed.</p>');
    }
}

async function handleOfflineCreateExercise(db, form) {
    const movement = await getByKey(db, 'movements', form.exercise_name);
    const bwRecord = await getByKey(db, 'user', 'bodyweight');

    const exercise = {
        id: 'offline_' + Date.now(),
        exercise_name: form.exercise_name,
        reps: parseInt(form.reps),
        weight: parseFloat(form.weight),
        rpe: parseFloat(form.rpe),
        bodyweight: (movement && movement.dip_belt && bwRecord) ? bwRecord.value : null,
        created_at: new Date().toISOString(),
    };

    await putItem(db, 'exercises', exercise);
    await queuePending(db, {
        type: 'create_exercise',
        data: {
            exercise_name: exercise.exercise_name,
            reps: exercise.reps,
            weight: exercise.weight,
            rpe: exercise.rpe,
        },
        created_at: exercise.created_at,
    });

    return htmlResponse('<div><p>Exercise logged</p></div>');
}

async function handleOfflineUpdateBodyweight(db, form) {
    const bw = parseFloat(form.bodyweight);
    await putItem(db, 'user', { key: 'bodyweight', value: bw });
    await queuePending(db, {
        type: 'update_bodyweight',
        data: { bodyweight: bw },
        created_at: new Date().toISOString(),
    });

    return htmlResponse(`
        <div id="bodyweight-display">
            <p style="font-size: 0.9rem; color: var(--pico-muted-color); margin-top: 0.5rem;">
                Current: <strong>${formatNumber(bw)}</strong>
            </p>
        </div>
    `);
}

async function handleOfflineCreateMovement(db, form) {
    const existing = await getByKey(db, 'movements', form.name);
    if (existing) {
        return htmlResponse(`
            <div class="failure-message">
            <p>Movement ${form.name} already exists</p>
            </div>
        `);
    }

    const movement = {
        name: form.name,
        dip_belt: form.dip_belt === 'on' || form.dip_belt === 'true',
    };
    await putItem(db, 'movements', movement);
    await queuePending(db, {
        type: 'create_movement',
        data: movement,
        created_at: new Date().toISOString(),
    });

    // Return response with HX-Trigger header so movement dropdowns refresh
    return new Response(`
        <div class="success-message">
        <p>Movement ${form.name} successfully created</p>
        </div>
    `, {
        headers: {
            'Content-Type': 'text/html; charset=utf-8',
            'HX-Trigger': 'movement-created',
        },
    });
}

async function handleOfflineRecommendation(db, form) {
    const exerciseName = form.exercise_name;
    const targetReps = parseInt(form.reps);
    const targetRpe = parseFloat(form.rpe);

    const movement = await getByKey(db, 'movements', exerciseName);
    if (!movement) {
        return htmlResponse(`
            <div class="failure-message">
            <p>Movement not found</p>
            </div>
        `);
    }

    // Find last exercise for this movement
    const allExercises = await getAll(db, 'exercises');
    const matchingExercises = allExercises
        .filter((e) => e.exercise_name === exerciseName)
        .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

    if (!matchingExercises.length) {
        return htmlResponse(`
            <div class="failure-message">
            <p>No previous data for this movement. Please log an exercise first to get a recommendation.</p>
            </div>
        `);
    }

    const last = matchingExercises[0];
    let bodyweight = 0;
    let lastBodyweight = 0;

    if (movement.dip_belt) {
        const bwRecord = await getByKey(db, 'user', 'bodyweight');
        bodyweight = bwRecord ? bwRecord.value : 70;
        lastBodyweight = last.bodyweight ? last.bodyweight : bodyweight;
    }

    // Calculate 1RM (same formula as server)
    const onerepmax = (last.weight + lastBodyweight) * 36 / (37 - (last.reps + (10 - last.rpe)));

    // Calculate target weight
    const targetR = targetReps + (10 - targetRpe);
    const totalWeight = onerepmax * (37 - targetR) / 36;
    const weight = totalWeight - bodyweight;
    const weightRounded = Math.round(weight / 1.25) * 1.25;

    return htmlResponse(`
        <form hx-post="/exercises/"
              hx-swap="none"
              hx-on::after-request="if(event.detail.successful) { var el = this.querySelector('.rec-success'); el.innerHTML = '<div class=\\'success-message\\'>Exercise logged successfully!</div>'; setTimeout(() => el.innerHTML = '', 3000); }">
            <table>
                <thead>
                    <tr>
                        <th>Movement</th>
                        <th>Reps</th>
                        <th>Weight (kg)</th>
                        <th>RPE</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>${exerciseName}<input type="hidden" name="exercise_name" value="${exerciseName}"></td>
                        <td>${targetReps}<input type="hidden" name="reps" value="${targetReps}"></td>
                        <td>${formatNumber(weightRounded)}<input type="hidden" name="weight" value="${weightRounded}"></td>
                        <td>
                            <input type="number" name="rpe" value="${formatNumber(targetRpe)}"
                                min="1" max="10" step="0.5" style="width: 5rem; margin: 0; padding: 0.25rem;">
                        </td>
                    </tr>
                </tbody>
            </table>
            <div class="rec-success"></div>
            <button type="submit">Log</button>
        </form>
    `);
}
