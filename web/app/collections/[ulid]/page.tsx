import {CollectionsView} from "../page";

export default async function CollectionPage({
  params,
}: {
  params: Promise<{ulid: string}>;
}) {
  const {ulid} = await params;
  return <CollectionsView initialUlid={ulid} />;
}
