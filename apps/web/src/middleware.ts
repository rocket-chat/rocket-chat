import { NextRequest, NextResponse } from "next/server";

export function middleware(request: NextRequest) {
  // Allow bypass in explicit test mode or disabled auth mode
  if (
    process.env.PLAYWRIGHT_TEST === "true" ||
    process.env.DISABLE_AUTH === "true"
  ) {
    return NextResponse.next();
  }

  // Check unified backend session cookie (or legacy NextAuth cookie during transition)
  const sessionCookie =
    request.cookies.get("rocket_session")?.value ||
    request.cookies.get("__Secure-next-auth.session-token")?.value ||
    request.cookies.get("next-auth.session-token")?.value;

  if (!sessionCookie) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("return_to", request.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - /login (authentication entry point)
     * - /api/auth/:path* (SSO and authentication routes)
     * - /_next/:path* (Next.js assets and client bundles)
     * - /favicon.ico, /icon.svg, /apple-icon.png (static icons)
     */
    "/((?!login|api/auth|_next/static|_next/image|favicon.ico|icon.svg|apple-icon.png).*)",
  ],
};
