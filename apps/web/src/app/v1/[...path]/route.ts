import { NextRequest, NextResponse } from "next/server";

function getBackendUrl(): string {
  if (process.env.BACKEND_URL) {
    return process.env.BACKEND_URL;
  }
  // When running inside Kubernetes cluster, kubelet injects KUBERNETES_SERVICE_HOST
  if (process.env.KUBERNETES_SERVICE_HOST) {
    return "http://rocket-chat-backend:8000";
  }
  return "http://127.0.0.1:8000";
}

import { getToken } from "next-auth/jwt";

async function handleProxy(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  const { path } = await params;
  const backendBase = getBackendUrl();
  const subPath = (path || []).join("/");
  const targetUrl = new URL(`/v1/${subPath}`, backendBase);

  // Preserve query parameters
  request.nextUrl.searchParams.forEach((value, key) => {
    targetUrl.searchParams.append(key, value);
  });

  const headers = new Headers();
  request.headers.forEach((val, key) => {
    const lower = key.toLowerCase();
    // Exclude hop-by-hop headers and host headers that would break reverse proxying
    if (!["host", "connection", "content-length", "transfer-encoding"].includes(lower)) {
      headers.set(key, val);
    }
  });

  // Extract session token from cookies via next-auth/jwt
  let token = null;
  try {
    token = await getToken({
      req: request,
      secret: process.env.NEXTAUTH_SECRET || "rocket-chat-development-nextauth-secret-key-32-chars-min",
    });
  } catch {
    token = null;
  }

  // Public exceptions for health or explicit test bypass mode
  const isTestBypass = process.env.PLAYWRIGHT_TEST === "true" || process.env.DISABLE_AUTH === "true";
  const isPublicRoute = subPath === "health" || subPath.startsWith("health/");
  if (!token && !isPublicRoute && !isTestBypass) {
    return NextResponse.json(
      { error: "Unauthorized", detail: "Authentication required to access API" },
      { status: 401 }
    );
  }

  if (token) {
    if (token.idToken) {
      headers.set("Authorization", `Bearer ${token.idToken}`);
    } else if (token.accessToken) {
      headers.set("Authorization", `Bearer ${token.accessToken}`);
    }
    if (token.userId) {
      headers.set("X-Tenant-User-Id", String(token.userId));
    }
    if (token.orgId) {
      headers.set("X-Tenant-Org-Id", String(token.orgId));
    }
    if (token.roles && Array.isArray(token.roles)) {
      headers.set("X-User-Role", token.roles.join(","));
    } else {
      headers.set("X-User-Role", "user");
    }
  } else if (isTestBypass) {
    headers.set("X-Tenant-User-Id", "test_user");
    headers.set("X-Tenant-Org-Id", "default_org");
    headers.set("X-User-Role", "developer,admin");
  }

  const method = request.method;
  const body = ["GET", "HEAD"].includes(method) ? undefined : await request.arrayBuffer();

  try {
    const upstreamRes = await fetch(targetUrl.toString(), {
      method,
      headers,
      body,
      // @ts-expect-error Node fetch option for streaming bodies
      duplex: "half",
    });

    const responseHeaders = new Headers();
    upstreamRes.headers.forEach((val, key) => {
      const lower = key.toLowerCase();
      if (!["content-encoding", "transfer-encoding"].includes(lower)) {
        responseHeaders.set(key, val);
      }
    });

    return new NextResponse(upstreamRes.body, {
      status: upstreamRes.status,
      statusText: upstreamRes.statusText,
      headers: responseHeaders,
    });
  } catch (err: unknown) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    console.error(`[API Proxy Error] Failed to reach ${targetUrl.toString()}:`, errorMsg);
    return NextResponse.json(
      {
        error: "Backend upstream unavailable",
        message: errorMsg,
        target: targetUrl.toString(),
      },
      { status: 502 }
    );
  }
}

export const GET = handleProxy;
export const POST = handleProxy;
export const PUT = handleProxy;
export const PATCH = handleProxy;
export const DELETE = handleProxy;
export const OPTIONS = handleProxy;
