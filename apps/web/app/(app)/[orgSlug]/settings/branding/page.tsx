import { BrandingForm } from "@/components/branding-form";
import { getLogoDataUri, getSettings } from "@/lib/settings";
import { requireOrganization } from "@/lib/session";

export default async function Page({ params }: PageProps<"/[orgSlug]/settings/branding">) {
  const { orgSlug } = await params;
  const { organization } = await requireOrganization(orgSlug);
  const settings = await getSettings(organization.id);
  const logo = settings.branding.has_logo ? await getLogoDataUri(organization.id) : null;

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">Report branding</h1>
        <p className="text-sm text-muted-foreground">
          Reports for {organization.name} carry your name, colour and logo. The product name never appears on them.
        </p>
      </div>
      <BrandingForm
        orgSlug={orgSlug}
        companyName={settings.branding.company_name ?? ""}
        primaryColor={settings.branding.primary_color}
        logo={logo}
        canManage={settings.can_manage}
      />
    </section>
  );
}
