export type PresetTransport = "http" | "sse" | "stdio";
export type PresetAuth = "none" | "api_key" | "oauth2" | "custom_headers";

export interface McpPreset {
  id: string;
  name: string;
  transport: PresetTransport;
  /** The remote URL, or the command a session's sandbox runs. */
  endpoint: string;
  auth: PresetAuth;
  /** How to get a credential, in one sentence. */
  setup: string;
  docsUrl: string;
  /** Environment variables a command server reads (stdio only). */
  env?: Array<{ name: string; secret?: boolean; memberOverride?: boolean }>;
  /** Script that installs the program the command runs, for servers that are not npx/uvx packages. */
  install?: string;
}

/**
 * Starting points for the registration form. They only prefill it - the server is registered
 * through the same flow as any other - and the vendor's page is linked because remote URLs and
 * package names change; check it if a connection fails.
 */
export const MCP_PRESETS: McpPreset[] = [
  {
    id: "github",
    name: "GitHub",
    transport: "http",
    endpoint: "https://api.githubcopilot.com/mcp/",
    auth: "api_key",
    setup: "Use a personal access token (or a fine-grained one scoped to the repositories you need).",
    docsUrl: "https://github.com/github/github-mcp-server",
  },
  {
    id: "linear",
    name: "Linear",
    transport: "http",
    endpoint: "https://mcp.linear.app/mcp",
    auth: "oauth2",
    setup: "Sign in with Linear after adding it. Alternatively choose API Key and use a Linear API key.",
    docsUrl: "https://linear.app/docs/mcp",
  },
  {
    id: "notion",
    name: "Notion",
    transport: "http",
    endpoint: "https://mcp.notion.com/mcp",
    auth: "oauth2",
    setup: "Sign in with Notion after adding it; it only sees the pages you share with it.",
    docsUrl: "https://developers.notion.com/docs/mcp",
  },
  {
    id: "atlassian",
    name: "Atlassian (Jira & Confluence)",
    transport: "sse",
    endpoint: "https://mcp.atlassian.com/v1/sse",
    auth: "oauth2",
    setup: "Sign in with your Atlassian account after adding it (an organization admin may need to approve the app).",
    docsUrl: "https://www.atlassian.com/platform/remote-mcp-server",
  },
  {
    id: "slack",
    name: "Slack",
    transport: "http",
    endpoint: "https://mcp.slack.com/mcp",
    auth: "oauth2",
    setup: "Sign in with Slack after adding it; your workspace may need to approve the app.",
    docsUrl: "https://docs.slack.dev/ai/mcp-server",
  },
  {
    id: "sentry",
    name: "Sentry",
    transport: "http",
    endpoint: "https://mcp.sentry.dev/mcp",
    auth: "oauth2",
    setup: "Sign in with Sentry after adding it.",
    docsUrl: "https://docs.sentry.io/product/sentry-mcp/",
  },
  {
    id: "stripe",
    name: "Stripe",
    transport: "http",
    endpoint: "https://mcp.stripe.com",
    auth: "api_key",
    setup: "Use a restricted API key limited to what the agent may do; prefer a test-mode key.",
    docsUrl: "https://docs.stripe.com/mcp",
  },
  {
    id: "cloudflare",
    name: "Cloudflare",
    transport: "http",
    endpoint: "https://docs.mcp.cloudflare.com/mcp",
    auth: "none",
    setup: "This endpoint serves Cloudflare's documentation. Other Cloudflare servers are listed on the page.",
    docsUrl: "https://developers.cloudflare.com/agents/model-context-protocol/mcp-servers-for-cloudflare/",
  },
  {
    id: "figma",
    name: "Figma",
    transport: "http",
    endpoint: "https://mcp.figma.com/mcp",
    auth: "oauth2",
    setup: "Sign in with Figma after adding it.",
    docsUrl: "https://help.figma.com/hc/en-us/articles/32132100833559",
  },
  {
    id: "vercel",
    name: "Vercel",
    transport: "http",
    endpoint: "https://mcp.vercel.com",
    auth: "oauth2",
    setup: "Sign in with Vercel after adding it.",
    docsUrl: "https://vercel.com/docs/mcp/vercel-mcp",
  },
  {
    id: "supabase",
    name: "Supabase",
    transport: "http",
    endpoint: "https://mcp.supabase.com/mcp",
    auth: "api_key",
    setup: "Use a personal access token as the API key (or choose OAuth and sign in). Prefer a development project.",
    docsUrl: "https://supabase.com/docs/guides/getting-started/mcp",
  },
  {
    id: "context7",
    name: "Context7 (library docs)",
    transport: "http",
    endpoint: "https://mcp.context7.com/mcp",
    auth: "none",
    setup: "Works without a key at a low rate limit; an API key from context7.com raises it.",
    docsUrl: "https://github.com/upstash/context7",
  },
  {
    id: "filesystem",
    name: "Filesystem",
    transport: "stdio",
    endpoint: "npx -y @modelcontextprotocol/server-filesystem /workspace",
    auth: "none",
    setup: "Gives the agent file tools limited to the folders you list after the package name.",
    docsUrl: "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
  },
  {
    id: "git",
    name: "Git",
    transport: "stdio",
    endpoint: "uvx mcp-server-git --repository /workspace/your-repo",
    auth: "none",
    setup: "Replace the repository path with a clone in the session's workspace.",
    docsUrl: "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
  },
  {
    id: "fetch",
    name: "Fetch",
    transport: "stdio",
    endpoint: "uvx mcp-server-fetch",
    auth: "none",
    setup: "Fetches web pages as Markdown. The built-in web_fetch tool already covers most uses.",
    docsUrl: "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
  },
  {
    id: "memory",
    name: "Memory (knowledge graph)",
    transport: "stdio",
    endpoint: "npx -y @modelcontextprotocol/server-memory",
    auth: "none",
    setup: "A simple knowledge-graph memory kept in the session's sandbox.",
    docsUrl: "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
  },
  {
    id: "postgres",
    name: "PostgreSQL",
    transport: "stdio",
    endpoint: "uvx postgres-mcp --access-mode=restricted",
    auth: "custom_headers",
    env: [{ name: "DATABASE_URI", secret: true }],
    setup: "Set DATABASE_URI to a connection string for a read-only role.",
    docsUrl: "https://github.com/crystaldba/postgres-mcp",
  },
  {
    id: "mongodb",
    name: "MongoDB",
    transport: "stdio",
    endpoint: "npx -y mongodb-mcp-server --readOnly",
    auth: "custom_headers",
    env: [{ name: "MDB_MCP_CONNECTION_STRING", secret: true }],
    setup: "Set MDB_MCP_CONNECTION_STRING to a connection string for a read-only user.",
    docsUrl: "https://github.com/mongodb-js/mongodb-mcp-server",
  },
  {
    id: "brave-search",
    name: "Brave Search",
    transport: "stdio",
    endpoint: "npx -y @brave/brave-search-mcp-server",
    auth: "custom_headers",
    env: [{ name: "BRAVE_API_KEY", secret: true, memberOverride: true }],
    setup: "Set BRAVE_API_KEY from brave.com/search/api.",
    docsUrl: "https://github.com/brave/brave-search-mcp-server",
  },
  {
    id: "grafana",
    name: "Grafana",
    transport: "stdio",
    endpoint: "uvx mcp-grafana",
    auth: "custom_headers",
    env: [
      { name: "GRAFANA_URL" },
      { name: "GRAFANA_API_KEY", secret: true, memberOverride: true },
    ],
    setup: "Set GRAFANA_URL and a read-only service account token as GRAFANA_API_KEY. Members can use their own token.",
    docsUrl: "https://github.com/grafana/mcp-grafana",
  },
  {
    id: "signoz",
    name: "SigNoz (release binary)",
    transport: "stdio",
    endpoint: "signoz-mcp-server",
    auth: "custom_headers",
    env: [
      { name: "SIGNOZ_URL" },
      { name: "SIGNOZ_API_KEY", secret: true, memberOverride: true },
    ],
    install:
      'case "$(uname -m)" in x86_64) a=amd64;; aarch64|arm64) a=arm64;; esac\n' +
      'curl -fsSL "https://github.com/SigNoz/signoz-mcp-server/releases/latest/download/signoz-mcp-server_linux_${a}.tar.gz" | tar xz',
    setup:
      "Not an npx/uvx package: the install script downloads the release binary once and the command runs it. Check the variable names and the binary's name against the project's README.",
    docsUrl: "https://github.com/SigNoz/signoz-mcp-server",
  },
  {
    id: "kubernetes",
    name: "Kubernetes",
    transport: "stdio",
    endpoint: "npx -y mcp-server-kubernetes",
    auth: "none",
    setup: "Uses the kubeconfig inside the sandbox, so it only works if you provide one there.",
    docsUrl: "https://github.com/Flux159/mcp-server-kubernetes",
  },
];
