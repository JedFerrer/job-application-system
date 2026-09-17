export interface TabAttemptContext {
  attemptId: string;
  applicationId: number;
  jobId: number;
  expiresAt: string;
  host: string;
  bindingState?: "bound" | "reconfirmation_required";
  /** Privacy-safe URL accepted by the backend during the successful bind. */
  boundPageUrl?: string;
}

const attemptKey = (tabId: number) => `attempt_context:${tabId}`;
const fieldsKey = (tabId: number) => `detected_fields:${tabId}`;

export async function getAttemptContext(tabId: number): Promise<TabAttemptContext | null> {
  const value = await chrome.storage.session.get(attemptKey(tabId));
  const context = value[attemptKey(tabId)] as TabAttemptContext | undefined;
  if (!context || Date.parse(context.expiresAt) <= Date.now()) {
    await chrome.storage.session.remove(attemptKey(tabId));
    return null;
  }
  return context;
}

export async function setAttemptContext(tabId: number, context: TabAttemptContext): Promise<void> {
  await chrome.storage.session.set({ [attemptKey(tabId)]: context });
}

export async function clearTabContext(tabId: number): Promise<void> {
  await chrome.storage.session.remove([attemptKey(tabId), fieldsKey(tabId), `detected_job:${tabId}`]);
}

export async function clearAllTabContexts(): Promise<void> {
  const values = await chrome.storage.session.get(null);
  const keys = Object.keys(values).filter((key) => key.startsWith("attempt_context:") || key.startsWith("detected_fields:") || key.startsWith("detected_job:") || key === "detected_fields" || key === "detected_fields_domain" || key === "detected_fields_ats_type");
  if (keys.length) await chrome.storage.session.remove(keys);
}

export async function setDetectedFields(tabId: number, fields: unknown): Promise<void> {
  await chrome.storage.session.set({ [fieldsKey(tabId)]: fields });
}

export async function getDetectedFields(tabId: number): Promise<unknown> {
  const values = await chrome.storage.session.get(fieldsKey(tabId));
  return values[fieldsKey(tabId)];
}
