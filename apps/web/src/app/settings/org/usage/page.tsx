import { redirect } from "next/navigation";

// Moved: usage and spend limits are on the Spend page.
export default function Page() {
  redirect("/settings/org/spend");
}
