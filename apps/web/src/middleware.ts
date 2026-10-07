import { withAuth } from "next-auth/middleware";
import { NextResponse } from "next/server";

export default withAuth(
  function middleware() {
    return NextResponse.next();
  },
  {
    callbacks: {
      authorized: ({ token }) => {
        // Allow bypass in explicit test mode
        if (
          process.env.PLAYWRIGHT_TEST === "true" ||
          process.env.DISABLE_AUTH === "true"
        ) {
          return true;
        }
        return !!token;
      },
    },
    pages: {
      signIn: "/login",
    },
  }
);

export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - /login (authentication entry point)
     * - /api/auth/:path* (NextAuth internal routes)
     * - /_next/:path* (Next.js assets and client bundles)
     * - /favicon.ico, /icon.svg, /apple-icon.png (static icons)
     */
    "/((?!login|api/auth|_next/static|_next/image|favicon.ico|icon.svg|apple-icon.png).*)",
  ],
};
