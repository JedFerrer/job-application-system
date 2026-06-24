import { NextRequest, NextResponse } from "next/server";

// Extend timeout to 120s — ingest can take 60+ seconds (LinkedIn scrape + embeddings).
// This replaces the next.config.ts rewrite for /api/backend/* which uses Node's
// default socket timeout and kills long-running proxied requests.
export const maxDuration = 120;

const BACKEND_URL =
  process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8000";

async function proxy(req: NextRequest, path: string[]) {
  const targetPath = path.join("/");
  const search = req.nextUrl.search;
  const url = `${BACKEND_URL}/api/v1/${targetPath}${search}`;

  const headers = new Headers(req.headers);
  headers.delete("host");

  const body =
    req.method !== "GET" && req.method !== "HEAD"
      ? await req.arrayBuffer()
      : undefined;

  const response = await fetch(url, {
    method: req.method,
    headers,
    body: body ?? null,
    // @ts-expect-error -- Node.js fetch signal for abort
    signal: AbortSignal.timeout(115_000),
  });

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
