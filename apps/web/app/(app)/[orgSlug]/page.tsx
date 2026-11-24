import { redirect } from "next/navigation";

export default async function Page({ params }: PageProps<"/[orgSlug]">) {
  const { orgSlug } = await params;
  redirect(`/${orgSlug}/clusters`);
}
