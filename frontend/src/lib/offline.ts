"use client";

/**
 * Device-local offline store for event dossiers.
 *
 * This is deliberately separate from the account-bound bookmark / reading-list
 * APIs. "Save offline" is a statement about *this device*: the reader wants the
 * dossier available without a connection, possibly without being signed in at
 * all. So the snapshot lives in IndexedDB and never leaves the browser.
 *
 * A snapshot is a frozen copy. The Watchtower refuses to serve cached news as if it
 * were live, so every surface that reads from here must label the copy with the
 * time it was saved rather than presenting it as current.
 */

export interface OfflineSource {
  name: string;
  credibility_score: number;
  bias_lean: string;
}

export interface OfflineArticle {
  id: string;
  citation_index: number | null;
  title: string;
  content: string;
  url: string;
  published_at?: string;
  source: OfflineSource;
}

export interface OfflineContradiction {
  nature: string;
  detail: string;
  claim_a: { text: string; source: string };
  claim_b: { text: string; source: string };
}

export interface OfflineEvent {
  id: string;
  title: string;
  summary: string;
  topic: string;
  country: string;
  latitude: number;
  longitude: number;
  importance_score: number;
  bias_lean: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  articles: OfflineArticle[];
  contradictions: OfflineContradiction[];
}

export interface SavedEvent {
  id: string;
  savedAt: number;
  title: string;
  topic: string;
  country: string;
  importance_score: number;
  created_at?: string;
  event: OfflineEvent;
}

const DB_NAME = "watchtower-offline";
const DB_VERSION = 1;
const STORE = "saved-events";

function supported(): boolean {
  return typeof indexedDB !== "undefined";
}

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

// Runs one request against the store and closes the connection when the
// transaction settles, so short-lived pages do not leak handles.
function withStore<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest
): Promise<T> {
  return openDB().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const tx = db.transaction(STORE, mode);
        const req = run(tx.objectStore(STORE));
        req.onsuccess = () => resolve(req.result as T);
        req.onerror = () => reject(req.error);
        tx.oncomplete = () => db.close();
        tx.onabort = () => {
          db.close();
          reject(tx.error);
        };
      })
  );
}

export async function saveEventForOffline(event: OfflineEvent): Promise<SavedEvent> {
  const saved: SavedEvent = {
    id: event.id,
    savedAt: Date.now(),
    title: event.title,
    topic: event.topic,
    country: event.country,
    importance_score: event.importance_score,
    created_at: event.created_at,
    event,
  };
  await withStore("readwrite", (store) => store.put(saved));
  return saved;
}

export function getSavedEvent(id: string): Promise<SavedEvent | undefined> {
  if (!supported()) return Promise.resolve(undefined);
  return withStore<SavedEvent | undefined>("readonly", (store) => store.get(id)).catch(
    () => undefined
  );
}

export async function isEventSaved(id: string): Promise<boolean> {
  const hit = await getSavedEvent(id);
  return !!hit;
}

export function removeEventForOffline(id: string): Promise<void> {
  if (!supported()) return Promise.resolve();
  return withStore<undefined>("readwrite", (store) => store.delete(id))
    .then(() => undefined)
    .catch(() => undefined);
}

export function listSavedEvents(): Promise<SavedEvent[]> {
  if (!supported()) return Promise.resolve([]);
  return withStore<SavedEvent[]>("readonly", (store) => store.getAll())
    .then((rows) => (rows || []).sort((a, b) => b.savedAt - a.savedAt))
    .catch(() => []);
}
