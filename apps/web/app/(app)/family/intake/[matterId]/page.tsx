import { IntakeWizard } from '@/components/family/intake/intake-wizard';

interface IntakeMatterPageProps {
  params: Promise<{ matterId: string }>;
}

/** Guided intake wizard for one matter. */
export default async function IntakeMatterPage({
  params,
}: IntakeMatterPageProps): Promise<React.JSX.Element> {
  const { matterId } = await params;
  return <IntakeWizard matterId={matterId} />;
}
