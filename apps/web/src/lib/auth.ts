import { NextAuthOptions } from "next-auth";
import GithubProvider from "next-auth/providers/github";
import GoogleProvider from "next-auth/providers/google";
import CredentialsProvider from "next-auth/providers/credentials";

export const authOptions: NextAuthOptions = {
  providers: [
    // Google OAuth provider (configured when GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are set)
    ...(process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET
      ? [
          GoogleProvider({
            clientId: process.env.GOOGLE_CLIENT_ID,
            clientSecret: process.env.GOOGLE_CLIENT_SECRET,
            authorization: {
              params: {
                prompt: "select_account",
                access_type: "offline",
                response_type: "code",
                // Pass optional hd hint if provided
                ...(process.env.GOOGLE_ALLOWED_DOMAIN ? { hd: process.env.GOOGLE_ALLOWED_DOMAIN } : {}),
              },
            },
            profile(profile) {
              const email = profile.email || "";
              const domain = email.includes("@") ? email.split("@")[1] : "default_org";
              const orgId = process.env.AUTH_DEFAULT_ORG || domain.replace(/[^a-zA-Z0-9]/g, "_");
              return {
                id: profile.sub,
                name: profile.name || email,
                email,
                image: profile.picture,
                orgId,
                roles: ["developer"],
              };
            },
          }),
        ]
      : []),

    // GitHub OAuth provider (configured when GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET are set)
    ...(process.env.GITHUB_CLIENT_ID && process.env.GITHUB_CLIENT_SECRET
      ? [
          GithubProvider({
            clientId: process.env.GITHUB_CLIENT_ID,
            clientSecret: process.env.GITHUB_CLIENT_SECRET,
            authorization: {
              params: {
                scope: "read:user user:email repo",
              },
            },
          }),
        ]
      : []),

    // Generic OIDC / Custom OAuth provider
    ...(process.env.OIDC_ISSUER && process.env.OIDC_CLIENT_ID && process.env.OIDC_CLIENT_SECRET
      ? [
          {
            id: "oidc",
            name: "Corporate SSO (OIDC)",
            type: "oauth" as const,
            wellKnown: `${process.env.OIDC_ISSUER.replace(/\/$/, "")}/.well-known/openid-configuration`,
            authorization: {
              params: {
                scope: "openid email profile",
                ...(process.env.OIDC_ALLOWED_DOMAIN ? { hd: process.env.OIDC_ALLOWED_DOMAIN } : {}),
              },
            },
            idToken: true,
            clientId: process.env.OIDC_CLIENT_ID,
            clientSecret: process.env.OIDC_CLIENT_SECRET,
            profile(profile: { sub: string; name?: string; email?: string; org_id?: string; roles?: string[]; hd?: string }) {
              const email = profile.email || "";
              const domain = profile.hd || (email.includes("@") ? email.split("@")[1] : "default_org");
              const orgId = profile.org_id || process.env.AUTH_DEFAULT_ORG || domain.replace(/[^a-zA-Z0-9]/g, "_");
              return {
                id: profile.sub,
                name: profile.name || profile.email,
                email,
                orgId,
                roles: profile.roles || ["developer"],
              };
            },
          },
        ]
      : []),

    // Local / Dev credentials fallback when no IdP is configured (e.g. local offline deployment)
    CredentialsProvider({
      id: "dev-login",
      name: "Local Developer Access",
      credentials: {
        username: { label: "Username", type: "text", placeholder: "developer" },
        orgId: { label: "Organization ID", type: "text", placeholder: "default_org" },
      },
      async authorize(credentials) {
        if (!credentials?.username) return null;
        const orgId = credentials.orgId || "default_org";
        return {
          id: `usr_${credentials.username.toLowerCase().replace(/[^a-z0-9]/g, "_")}`,
          name: credentials.username,
          email: `${credentials.username.toLowerCase()}@${orgId}.internal`,
          orgId,
          roles: ["developer", "admin"],
        };
      },
    }),
  ],

  callbacks: {
    async signIn({ user, account }) {
      const email = user.email || "";
      const userDomain = email.includes("@") ? email.split("@")[1].toLowerCase() : "";

      // Check organization domain restriction if configured
      try {
        const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000";
        const res = await fetch(`${backendUrl}/v1/settings/general`, {
          headers: { "X-Tenant-Org-Id": (user as unknown as { orgId?: string }).orgId || "default_org" },
          cache: "no-store",
        });
        if (res.ok) {
          const data = await res.json();
          const effective = data.effective || {};
          const enforce = Boolean(effective.sso_enforce_domain_match);
          const rawAllowed = (effective.sso_allowed_domains as string) || "";
          const allowedList = rawAllowed
            .split(",")
            .map((d: string) => d.trim().toLowerCase())
            .filter(Boolean);

          if (enforce && allowedList.length > 0) {
            if (!userDomain || !allowedList.includes(userDomain)) {
              console.warn(`[NextAuth] Blocked sign-in for ${email}: domain ${userDomain} not in allowed list [${allowedList.join(", ")}]`);
              return false;
            }
          }
        }
      } catch (err) {
        console.warn("[NextAuth] Org domain policy check bypassed (backend unavailable):", err);
      }

      // Check environment variable fallback domain restriction
      const envAllowedDomain = process.env.GOOGLE_ALLOWED_DOMAIN || process.env.OIDC_ALLOWED_DOMAIN;
      if (envAllowedDomain && (account?.provider === "google" || account?.provider === "oidc")) {
        const allowed = envAllowedDomain.split(",").map((d) => d.trim().toLowerCase());
        if (!userDomain || !allowed.includes(userDomain)) {
          console.warn(`[NextAuth] Blocked ${account?.provider} sign-in for ${email}: domain ${userDomain} does not match ${envAllowedDomain}`);
          return false;
        }
      }

      return true;
    },
    async jwt({ token, user, account }) {
      if (user) {
        token.userId = user.id;
        token.orgId = (user as unknown as { orgId?: string }).orgId || "default_org";
        token.roles = (user as unknown as { roles?: string[] }).roles || ["developer"];
      }
      if (account?.access_token) {
        token.accessToken = account.access_token;
      }
      if (account?.id_token) {
        token.idToken = account.id_token;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        (session.user as unknown as { id: string }).id = (token.userId as string) || (token.sub as string);
        (session.user as unknown as { orgId: string }).orgId = (token.orgId as string) || "default_org";
        (session.user as unknown as { roles: string[] }).roles = (token.roles as string[]) || ["developer"];
        (session.user as unknown as { accessToken?: string }).accessToken = token.accessToken as string | undefined;
      }
      return session;
    },
  },

  pages: {
    signIn: "/login",
    error: "/login",
  },
  session: {
    strategy: "jwt",
    maxAge: 30 * 24 * 60 * 60, // 30 days
  },
  secret: process.env.NEXTAUTH_SECRET || "rocket-chat-development-nextauth-secret-key-32-chars-min",
};
