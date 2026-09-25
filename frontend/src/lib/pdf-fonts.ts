import { createRetryableLoader } from "./retryable-loader";

const FONT_URLS = {
  regular: "/fonts/NotoSans-Regular.ttf",
  bold: "/fonts/NotoSans-Bold.ttf",
} as const;

async function fetchFont(url: string): Promise<string> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`PDF font request failed: ${response.status}`);

  const bytes = new Uint8Array(await response.arrayBuffer());
  const parts: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    parts.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
  }
  return btoa(parts.join(""));
}

/** Share pending requests and successful fonts; a failed pair can be retried. */
export const loadPdfFonts = createRetryableLoader(async () => {
  const [regular, bold] = await Promise.all([
    fetchFont(FONT_URLS.regular),
    fetchFont(FONT_URLS.bold),
  ]);
  return { regular, bold };
});
