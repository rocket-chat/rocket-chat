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
    // With the query: an invitation link carries its token there.
    loginUrl.searchParams.set("return_to", request.nextUrl.pathname + request.nextUrl.search);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - /login (authentication entry point)
     * - /shared/:token (a public session link: its signed token is the credential)
     * - /api/auth/:path* (SSO and authentication routes)
     * - /_next/:path* (Next.js assets and client bundles)
     * - /favicon.ico, /icon.svg, /apple-icon.png (Next.js convention icons)
     * - the public/ logo assets (components fetch these directly, e.g. RocketChatIcon)
     */
    "/((?!login|shared/|api/auth|_next/static|_next/image|favicon.ico|icon.svg|apple-icon.png|favicon.svg|favicon-16x16.png|favicon-32x32.png|apple-touch-icon.png|logo.svg).*)",
  ],
};
