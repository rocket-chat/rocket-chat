import { redirect } from "next/navigation";

// Moved: the model preference is under Preferences, keys under My keys.
export default function Page() {
  redirect("/settings/user/preferences");
}
