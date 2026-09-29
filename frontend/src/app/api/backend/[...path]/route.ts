import { NextRequest, NextResponse } from "next/server";

// Extend timeout to 120s — ingest can take 60+ seconds (LinkedIn scrape + embeddings).
// This replaces the next.config.ts rewrite for /api/backend/* which uses Node's
// default socket timeout and kills long-running proxied requests.
export const maxDuration = 120;

const BACKEND_URL =
  process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8000";

const SAFE_ERROR_NAMES = new Set(["TypeError", "AbortError", "TimeoutError"]);
const SAFE_ERROR_CODES = new Set([
  "ECONNREFUSED",
  "ECONNRESET",
  "ETIMEDOUT",
  "EHOSTUNREACH",
  "ENETUNREACH",
  "UND_ERR_CONNECT_TIMEOUT",
  "UND_ERR_HEADERS_TIMEOUT",
  "UND_ERR_SOCKET",
]);

function safeDiagnostic(value: unknown, allowed: Set<string>): string | undefined {
  return typeof value === "string" && allowed.has(value) ? value : undefined;
}

async function proxy(req: NextRequest, path: string[]) {
  const targetPath = path.join("/");
  const search = req.nextUrl.search;
  const url = `${BACKEND_URL}/api/v1/${targetPath}${search}`;
  const isRefresh = targetPath === "auth/refresh";
  const startedAt = Date.now();

  const headers = new Headers(req.headers);
  headers.delete("host");

  const body =
    req.method !== "GET" && req.method !== "HEAD"
      ? await req.arrayBuffer()
      : undefined;

  let response: Response;
  try {
    response = await fetch(url, {
      method: req.method,
      headers,
      body: body ?? null,
      signal: AbortSignal.timeout(115_000),
    });
  } catch (err) {
    // AbortSignal.timeout throws a DOMException("TimeoutError") after 115 s.
    // Other fetch failures (ECONNREFUSED, ECONNRESET, etc.) land here too.
    // We must NOT let them propagate unhandled — Node's DOMException has a
    // read-only `message` property that Next.js tries to set, causing:
    // "TypeError: Cannot set property message ... which has only a getter"
    const isTimeout =
      err instanceof DOMException && err.name === "TimeoutError";
    if (isRefresh) {
      const cause = err instanceof Error ? err.cause : undefined;
      console.warn("Auth refresh proxy request failed", {
        elapsedMs: Date.now() - startedAt,
        errorName: safeDiagnostic(
          err instanceof Error ? err.name : undefined,
          SAFE_ERROR_NAMES,
        ),
        errorCode: safeDiagnostic(
          cause && typeof cause === "object" && "code" in cause
            ? cause.code
            : undefined,
          SAFE_ERROR_CODES,
        ),
      });
    }
    return new NextResponse(
      JSON.stringify({
        detail: isTimeout
          ? "Backend request timed out"
          : "Backend unavailable",
      }),
      {
        status: isTimeout ? 504 : 502,
        headers: { "Content-Type": "application/json" },
      },
    );
  }

  if (isRefresh && !response.ok) {
    console.warn("Auth refresh proxy returned an error", {
      elapsedMs: Date.now() - startedAt,
      status: response.status,
    });
  }

  const resHeaders = new Headers(response.headers);
  resHeaders.delete("transfer-encoding");

  return new NextResponse(response.body, {
    status: response.status,
    headers: resHeaders,
  });
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return proxy(req, path);
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return proxy(req, path);
}

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return proxy(req, path);
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return proxy(req, path);
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return proxy(req, path);
}
