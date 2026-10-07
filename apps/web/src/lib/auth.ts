import { NextAuthOptions } from "next-auth";
import GithubProvider from "next-auth/providers/github";
import CredentialsProvider from "next-auth/providers/credentials";

export const authOptions: NextAuthOptions = {
  providers: [
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
            authorization: { params: { scope: "openid email profile" } },
            idToken: true,
            clientId: process.env.OIDC_CLIENT_ID,
            clientSecret: process.env.OIDC_CLIENT_SECRET,
            profile(profile: { sub: string; name?: string; email?: string; org_id?: string; roles?: string[] }) {
              return {
                id: profile.sub,
                name: profile.name || profile.email,
                email: profile.email,
                orgId: profile.org_id || "default_org",
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
  },
  session: {
    strategy: "jwt",
    maxAge: 30 * 24 * 60 * 60, // 30 days
  },
  secret: process.env.NEXTAUTH_SECRET || "rocket-chat-development-nextauth-secret-key-32-chars-min",
};
