import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const hasGoogle = Boolean(
    process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET
  );
  const hasGithub = Boolean(
    process.env.GITHUB_CLIENT_ID && process.env.GITHUB_CLIENT_SECRET
  );
  const hasOidc = Boolean(
    process.env.OIDC_ISSUER &&
      process.env.OIDC_CLIENT_ID &&
      process.env.OIDC_CLIENT_SECRET
  );

  // Dev login is enabled if explicitly requested, in non-production mode, or if no SSO provider is configured
  const hasAnySSO = hasGoogle || hasGithub || hasOidc;
  const allowDev =
    process.env.ALLOW_DEV_LOGIN === "true" ||
    !hasAnySSO ||
    process.env.NODE_ENV !== "production";

  return NextResponse.json({
    google: hasGoogle,
    github: hasGithub,
    oidc: hasOidc,
    devLogin: allowDev,
  });
}
