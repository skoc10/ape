import { redirect } from "next/navigation";

import { AuthForm } from "@/components/auth-form";
import { safeNextPath } from "@/lib/safe-redirect";
import { getSession } from "@/lib/session";

export default async function Page({ searchParams }: PageProps<"/sign-in">) {
  const next = safeNextPath((await searchParams).next);
  if (await getSession()) {
    redirect(next);
  }
  return <AuthForm mode="sign-in" next={next} />;
}
