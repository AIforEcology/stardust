export interface Settings {
  apiBase: string;
  /** Site ids the user has opted in to measuring (spec §14.1: explicit opt-in). */
  enabledSites: string[];
  /** Pseudonymous id, generated locally (spec §14.1). */
  userId: string;
}

export const DEFAULT_API_BASE = "http://localhost:8080";

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
