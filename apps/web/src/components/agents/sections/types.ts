import type { AgentDraft } from "../draft";
import type { AgentRegistries } from "../useAgentRegistries";

/** What every section of the agent editor receives. */
export interface SectionProps {
  draft: AgentDraft;
  patch: (changes: Partial<AgentDraft>) => void;
  canEdit: boolean;
  isAdmin: boolean;
  registries: AgentRegistries;
}
