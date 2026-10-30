"use client";

import { useState } from "react";
import { MAX_GUIDANCE_CHARS } from "../../../../lib/mcp-guidance";
import type { McpRunnerStatus, McpServerConfig, McpToolSchema } from "../../../../types/mission";
import { MCP_PRESETS, type McpPreset } from "../presets";
import { useMcpAuthFields } from "./useMcpAuthFields";

export type Transport = McpServerConfig["transport"];
export type SignIn = "automatic" | "needs_client";
export type Step = "preset" | "connection" | "auth" | "tools";

export const STEP_LABELS: Record<Step, string> = {
  preset: "Preset",
  connection: "Connection",
  auth: "Authentication",
  tools: "Tools",
};

export interface ValidationResult {
  valid: boolean;
  message?: string;
  error?: string | null;
  tools_count?: number;
  tools?: McpToolSchema[];
  instructions?: string;
  sign_in?: SignIn | null;
}

export interface McpServerFormOptions {
  scope: "user" | "org";
  isAdmin: boolean;
  runner: McpRunnerStatus | null;
  editing?: McpServerConfig;
  onSaved: (server: McpServerConfig, message: string) => void;
}

function commandBlockerOf(canUseCommands: boolean, runner: McpRunnerStatus | null): string | null {
  if (!canUseCommands) return "Command servers are set up by an administrator - ask yours to add it";
  if (runner && runner.available !== true) {
    return runner.configured ? "The MCP runner is not answering" : "The MCP runner is not deployed";
  }
  return null;
}

async function postJson(url: string, body: unknown): Promise<Response> {
  return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

/** Everything the MCP server wizard holds, shared by its steps. */
export function useMcpServerForm({ scope, isAdmin, runner, editing, onSaved }: McpServerFormOptions) {
  const steps: Step[] = editing ? ["connection", "auth", "tools"] : ["preset", "connection", "auth", "tools"];
  const [step, setStep] = useState<Step>(steps[0]);
  const [preset, setPreset] = useState<McpPreset | null>(null);
  const [serverName, setServerName] = useState(editing?.name ?? "");
  const [transport, setTransport] = useState<Transport>(editing?.transport ?? "http");
  const [endpoint, setEndpoint] = useState(editing?.endpoint_or_command ?? "");
  const [guidance, setGuidance] = useState(editing?.guidance ?? "");
  // The server's own instructions start the guidance once, when it is empty, and say so: they are
  // the server's words and go to the agent only after the administrator has seen and saved them.
  const [guidanceNotice, setGuidanceNotice] = useState<string | null>(null);
  const [instructionsOffered, setInstructionsOffered] = useState(false);
  // "automatic" needs nothing set up, "needs_client" an OAuth app created on the provider.
  const [signIn, setSignIn] = useState<SignIn | null>(null);
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const auth = useMcpAuthFields(editing, scope);

  const isCommand = transport === "stdio";
  const canUseCommands = isAdmin && scope === "org";
  const commandBlocker = commandBlockerOf(canUseCommands, runner);
  // Saving opens the sign-in when the server is OAuth and this account is not connected yet.
  const signsIn = !isCommand && auth.authType === "oauth2" && editing?.oauth_status !== "connected";

  const applyPreset = (id: string) => {
    const chosen = MCP_PRESETS.find((p) => p.id === id) ?? null;
    setPreset(chosen);
    setValidation(null);
    if (!chosen) return;
    setServerName(chosen.name);
    setTransport(chosen.transport);
    setEndpoint(chosen.endpoint);
    auth.applyPreset(chosen);
  };

  const changeTransport = (next: Transport) => {
    setTransport(next);
    if (next === "stdio" && auth.authType !== "none" && auth.authType !== "custom_headers") auth.setAuthType("none");
  };

  const changeEndpoint = (next: string) => {
    setEndpoint(next);
    if (editing && next !== editing.endpoint_or_command) auth.forgetDiscoveredOAuth();
  };

  /** A server that asks for an account is an OAuth server: nothing else to choose. */
  const adoptSignIn = (offered: SignIn | null | undefined) => {
    if (!offered) return;
    setSignIn(offered);
    auth.setAuthType("oauth2");
  };

  /** As soon as an address is entered, ask the server whether it signs people in. */
  const detectSignIn = async (address: string) => {
    if (isCommand || editing || !/^https?:\/\//.test(address.trim())) return;
    if (auth.authType !== "none" && auth.authType !== "oauth2") return;
    try {
      const body = { transport, endpoint_or_command: address.trim(), scope, auth: { auth_type: "none" } };
      const res = await postJson("/v1/mcp/servers/validate", body);
      if (res.ok) adoptSignIn(((await res.json()) as ValidationResult).sign_in);
    } catch {
      // Detection is a convenience: the server can still be set up by hand.
    }
  };

  const offerInstructions = (instructions?: string) => {
    const text = (instructions ?? "").trim();
    if (!text || guidance.trim() || instructionsOffered) return;
    setInstructionsOffered(true);
    const tooLong = text.length > MAX_GUIDANCE_CHARS;
    setGuidance(tooLong ? text.slice(0, MAX_GUIDANCE_CHARS) : text);
    setGuidanceNotice(
      tooLong
        ? `Filled in from the server's own instructions (${text.length.toLocaleString()} characters, cut to the ${MAX_GUIDANCE_CHARS.toLocaleString()} allowed). Review it before saving.`
        : "Filled in from the server's own instructions. Review it before saving.",
    );
  };

  const editGuidance = (value: string) => {
    setGuidance(value);
    setGuidanceNotice(null);
  };

  const validate = async () => {
    if (!endpoint) {
      setValidation({ valid: false, message: "Please provide an endpoint URL or command" });
      return;
    }
    setValidating(true);
    setValidation(null);
    try {
      const body = { id: editing?.id, transport, endpoint_or_command: endpoint, scope, auth: auth.buildAuth(isCommand) };
      const res = await postJson("/v1/mcp/servers/validate", body);
      const data = (await res.json()) as ValidationResult & { detail?: string };
      if (!res.ok) {
        setValidation({ valid: false, message: data.detail || "Validation failed" });
        return;
      }
      setValidation(data);
      adoptSignIn(data.sign_in);
      if (data.valid) offerInstructions(data.instructions);
    } catch (err) {
      setValidation({ valid: false, message: err instanceof Error ? err.message : "Validation error occurred" });
    } finally {
      setValidating(false);
    }
  };

  const problemBeforeSave = (): string | null => {
    if (!serverName.trim() || !endpoint.trim()) return "Name and endpoint are required.";
    if (guidance.length > MAX_GUIDANCE_CHARS) {
      return `The guidance is ${guidance.length.toLocaleString()} characters; the limit is ${MAX_GUIDANCE_CHARS.toLocaleString()}.`;
    }
    return null;
  };

  const save = async () => {
    const problem = problemBeforeSave();
    setFormError(problem);
    if (problem) return;
    // Opened now, while the click still counts as the person's gesture (a tab opened after the
    // save would be blocked as a pop-up), and pointed at the sign-in once the server exists.
    const signInTab = signsIn ? window.open("", "_blank") : null;
    try {
      const res = await postJson("/v1/mcp/servers", {
        id: editing?.id,
        name: serverName.trim(),
        transport,
        endpoint_or_command: endpoint.trim(),
        scope,
        guidance: guidance.trim() || undefined,
        auth: auth.buildAuth(isCommand),
      });
      if (!res.ok) {
        const err: { detail?: string } = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to register MCP server");
      }
      const created: McpServerConfig = await res.json();
      if (signInTab) signInTab.location.href = `/v1/mcp/servers/${created.id}/oauth/authorize`;
      onSaved(created, editing ? `Updated MCP server '${created.name}'` : `Registered MCP server '${created.name}' (${scope} scope)`);
    } catch (err: unknown) {
      signInTab?.close();
      setFormError(err instanceof Error ? err.message : "Error saving MCP server");
    }
  };

  return {
    scope,
    isAdmin,
    editing,
    steps,
    step,
    setStep,
    stepIndex: steps.indexOf(step),
    preset,
    applyPreset,
    serverName,
    setServerName,
    transport,
    changeTransport,
    endpoint,
    changeEndpoint,
    detectSignIn,
    guidance,
    guidanceNotice,
    instructionsOffered,
    editGuidance,
    signIn,
    auth,
    isCommand,
    canUseCommands,
    commandBlocker,
    signsIn,
    validating,
    validation,
    validate,
    formError,
    save,
  };
}

export type McpServerFormState = ReturnType<typeof useMcpServerForm>;
