// --- Service Worker Registration ---

if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js');
}

// --- IndexedDB Setup ---

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

function putAll(db, storeName, items) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        store.clear();
        for (const item of items) {
            store.put(item);
        }
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

function putOne(db, storeName, item) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        store.put(item);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
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

function clearStore(db, storeName) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        store.clear();
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

// --- Replay pending offline writes ---

async function replayPendingActions() {
    const db = await openDB();
    const pending = await getAll(db, 'pending');

    if (!pending.length) return;

    console.log(`[offline] Replaying ${pending.length} pending action(s)`);
    const response = await fetch('/api/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ actions: pending }),
    });

    if (response.ok) {
        await clearStore(db, 'pending');
        console.log('[offline] Pending actions replayed successfully');
    } else {
        console.error('[offline] Failed to replay pending actions:', response.status);
    }
}

// --- Sync Logic ---

async function syncData() {
    try {
        // First, replay any pending offline writes
        await replayPendingActions();

        // Then pull fresh data from the server
        const response = await fetch('/api/sync');
        if (!response.ok) return;

        const data = await response.json();
        const db = await openDB();

        await putAll(db, 'movements', data.movements);
        await putAll(db, 'exercises', data.exercises);
        await putOne(db, 'user', { key: 'bodyweight', value: data.bodyweight });

        console.log('[offline] Synced data to IndexedDB');
    } catch (e) {
        // Offline or sync failed — not a problem, we'll use cached data
        console.log('[offline] Sync skipped (offline or error)');
    }
}

// Sync on page load
syncData();

// --- Offline Indicator ---

function updateOfflineIndicator() {
    const banner = document.getElementById('offline-banner');
    if (!banner) return;
    banner.hidden = navigator.onLine;
}

window.addEventListener('online', () => {
    updateOfflineIndicator();
    syncData(); // Re-sync when coming back online
});

window.addEventListener('offline', updateOfflineIndicator);

document.addEventListener('DOMContentLoaded', updateOfflineIndicator);
