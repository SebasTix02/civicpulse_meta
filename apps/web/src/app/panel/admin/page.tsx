import { currentLang } from "@/lib/lang-server";
import { Admin } from "./Admin";

// M1 + M3 (moderation queue and catalog), for city admins and catalog editors.
export default async function AdminPage() {
  const lang = await currentLang();
  return <Admin lang={lang} />;
}
