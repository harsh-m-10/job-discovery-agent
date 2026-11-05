import { redirect } from "next/navigation";

/** Contacts moved to PEOPLE (spec 002); old links keep working. */
export default async function ContactsPage({ params }: { params: Promise<{ secret: string }> }) {
  const { secret } = await params;
  redirect(`/d/${secret}/people`);
}
