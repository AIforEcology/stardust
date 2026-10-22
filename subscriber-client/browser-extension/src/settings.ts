export interface Settings {
  apiBase: string;
  /** Site ids the user has opted in to measuring (spec §14.1: explicit opt-in). */
  enabledSites: string[];
  /** Pseudonymous id, generated locally (spec §14.1). Used with a Core that doesn't issue keys. */
  userId: string;
}

/** A Core's answer to POST /v1/installs. `key` is null when that Core doesn't issue keys (a local Core). */
export interface Credentials {
  key: string | null;
  userId: string | null;
}

// Set at build time (build.mjs, STARDUST_API_BASE): the hosted Core for store builds, localhost otherwise.
declare const __STARDUST_DEFAULT_API_BASE__: string;
export const DEFAULT_API_BASE: string =
  typeof __STARDUST_DEFAULT_API_BASE__ === "string" ? __STARDUST_DEFAULT_API_BASE__ : "http://localhost:8080";

export async function getSettings(): Promise<Settings> {
  const s = await chrome.storage.local.get(["apiBase", "enabledSites", "userId"]);
  let userId = s.userId as string | undefined;
  if (!userId) {
    userId = crypto.randomUUID();
    await chrome.storage.local.set({ userId });
  }
  return {
    apiBase: (s.apiBase as string | undefined) ?? DEFAULT_API_BASE,
    enabledSites: (s.enabledSites as string[] | undefined) ?? [],
    userId,
  };
}

export async function setSiteEnabled(siteId: string, enabled: boolean): Promise<void> {
  const { enabledSites } = await getSettings();
  const next = new Set(enabledSites);
  if (enabled) next.add(siteId);
  else next.delete(siteId);
  await chrome.storage.local.set({ enabledSites: [...next] });
}

export async function setApiBase(apiBase: string): Promise<void> {
  await chrome.storage.local.set({ apiBase: apiBase.replace(/\/+$/, "") });
}

const credKey = (apiBase: string) => `auth:${apiBase}`;

/**
 * This install's key for a Core, registering on first use. Keys are kept per Core, so switching
 * between a hosted and a local Core never sends one Core's key to the other.
 */
export async function getCredentials(apiBase: string): Promise<Credentials> {
  const k = credKey(apiBase);
  const stored = (await chrome.storage.local.get(k))[k] as Credentials | undefined;
  if (stored) return stored;
  const r = await fetch(`${apiBase}/v1/installs`, { method: "POST" });
  let creds: Credentials;
  if (r.status === 404) {
    creds = { key: null, userId: null }; // this Core doesn't issue keys
  } else if (r.ok) {
    const body = (await r.json()) as { key: string; user_id: string };
    creds = { key: body.key, userId: body.user_id };
  } else {
    throw new Error(`Couldn't register with Stardust Core (${r.status})`);
  }
  await chrome.storage.local.set({ [k]: creds });
  return creds;
}

/** Forget a Core's key (it was revoked, or the Core was reset); the next call registers again. */
export async function clearCredentials(apiBase: string): Promise<void> {
  await chrome.storage.local.remove(credKey(apiBase));
}

/** Core revoked this install's key: the operator blocked it, so it must not register again. */
export class KeyRevokedError extends Error {
  constructor() {
    super("This install's key was revoked by the Stardust Core operator, so nothing is being sent. Contact them to restore it.");
    this.name = "KeyRevokedError";
  }
}

/** Whether a 401 says the key was revoked (Core adds key_revoked: true), as opposed to unknown. */
async function isRevoked(r: Response): Promise<boolean> {
  try {
    return (await r.clone().json())?.key_revoked === true;
  } catch {
    return false;
  }
}

/**
 * Fetch from Core with this install's key. If Core no longer knows the key (for example it was
 * reset), register once more and retry. If Core says the key was revoked, never re-register:
 * that would undo the revocation. Throws KeyRevokedError instead.
 */
export async function coreFetch(path: string, init: RequestInit = {}): Promise<{ r: Response; userId: string }> {
  const settings = await getSettings();
  for (let attempt = 0; ; attempt++) {
    const creds = await getCredentials(settings.apiBase);
    const headers = new Headers(init.headers);
    if (creds.key) headers.set("authorization", `Bearer ${creds.key}`);
    const r = await fetch(`${settings.apiBase}${path}`, { ...init, headers });
    if (r.status === 401 && creds.key) {
      if (await isRevoked(r)) throw new KeyRevokedError();
      if (attempt === 0) {
        await clearCredentials(settings.apiBase);
        continue;
      }
    }
    return { r, userId: creds.userId ?? settings.userId };
  }
}

/** The user id to put on events and summary queries for the current Core. */
export async function currentUserId(): Promise<string> {
  const settings = await getSettings();
  try {
    return (await getCredentials(settings.apiBase)).userId ?? settings.userId;
  } catch {
    return settings.userId;
  }
}
