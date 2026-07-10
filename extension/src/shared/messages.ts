// ── Typed Message Helpers ──────────────────────────────────────────────────
// Wraps chrome.runtime.sendMessage with type-safe helpers so callers get
// autocompletion on the message type and payload.

import type { ExtensionMessage } from "./types";

/** Send a message to the background service worker (or any listener). */
export function sendMessage<T extends ExtensionMessage>(
  message: T,
): Promise<ExtensionMessage> {
  return chrome.runtime.sendMessage(message);
}

/**
 * Send a message and expect a specific response type.
 * The caller provides a type guard to narrow the response.
 */
export async function sendMessageExpect<T extends ExtensionMessage>(
  message: ExtensionMessage,
  guard: (msg: ExtensionMessage) => msg is T,
): Promise<T> {
  const response = await chrome.runtime.sendMessage(message);
  if (guard(response)) {
    return response;
  }
  throw new Error(
    `Unexpected response type for message ${message.type}: ${JSON.stringify(response)}`,
  );
}

/** Type guard for ERROR responses. */
export function isError(
  msg: ExtensionMessage,
): msg is Extract<ExtensionMessage, { type: "ERROR" }> {
  return msg.type === "ERROR";
}

/** Add a typed listener for messages from content scripts or popup. */
export function onMessage(
  handler: (
    message: ExtensionMessage,
    sender: chrome.runtime.MessageSender,
    sendResponse: (response: ExtensionMessage) => void,
  ) => boolean | void,
): void {
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    // Ensure the message has our discriminant.
    if (msg && typeof msg === "object" && "type" in msg) {
      return handler(msg as ExtensionMessage, sender, sendResponse);
    }
    return false;
  });
}
