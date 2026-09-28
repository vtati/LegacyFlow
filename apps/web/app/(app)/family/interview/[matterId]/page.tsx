import { InterviewChat } from '@/components/family/interview/interview-chat';

interface InterviewMatterPageProps {
  params: Promise<{ matterId: string }>;
}

/** AI interviewer for one matter — runs alongside the guided intake form. */
export default async function InterviewMatterPage({
  params,
}: InterviewMatterPageProps): Promise<React.JSX.Element> {
  const { matterId } = await params;
  return <InterviewChat matterId={matterId} />;
}
