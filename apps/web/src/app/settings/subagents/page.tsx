import { redirect } from "next/navigation";

// Subagent roles are edited with the agents, in one editor.
export default function Page() {
  redirect("/settings/agents");
}
