import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const publicFonts = resolve(dirname(fileURLToPath(import.meta.url)), "../public/fonts");
const originalFetch = globalThis.fetch;

// Unit tests use the exact production TTF files without a running Next server.
globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const name = url === "/fonts/NotoSans-Regular.ttf" ? "NotoSans-Regular.ttf"
    : url === "/fonts/NotoSans-Bold.ttf" ? "NotoSans-Bold.ttf" : undefined;
  if (!name) return originalFetch(input, init);
  const bytes = readFileSync(resolve(publicFonts, name));
  return Promise.resolve({
    ok: true,
    status: 200,
    arrayBuffer: async () => Uint8Array.from(bytes).buffer,
  } as Response);
}) as typeof fetch;
