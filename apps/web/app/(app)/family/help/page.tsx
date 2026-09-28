'use client';

import { useState } from 'react';
import Link from 'next/link';
import { ChevronDown, CircleAlert } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';

interface Faq {
  question: string;
  answer: string[];
}

const FAQS: Faq[] = [
  {
    question: 'What is intake?',
    answer: [
      'Intake is the set of questions your firm asks so your attorney can prepare your plan. The questions are tailored to your jurisdiction, and your answers save automatically as you go — you can stop and resume any time from the Intake page.',
      'Once every required answer is in, you submit the matter. Submitting sends your answers to your firm so attorney review can begin. Nothing in intake is legal advice, and nothing is final until your attorney approves it.',
    ],
  },
  {
    question: 'What does attorney review mean?',
    answer: [
      'After you submit intake, your attorney reads your answers, checks them against the rules for your jurisdiction, and may ask follow-up questions (clarifications) before approving your plan.',
      'While a matter is in review you can watch its status from My Plan or Home. If your attorney needs something from you, it appears as an open item in Checkup with a link to the page that resolves it.',
    ],
  },
  {
    question: 'Why are documents watermarked before release?',
    answer: [
      'The documents you see on the Documents page are demo drafts for review only — they are not legal documents. Every draft carries a watermark so it cannot be mistaken for a final, executed version.',
      'Once your attorney has reviewed and approved your plan, the firm releases the production documents. Only released, executed documents have legal effect; the watermarked drafts are there so you can check that names, roles, and details are correct before anything becomes final.',
    ],
  },
  {
    question: 'How does signing work?',
    answer: [
      'When your plan is ready to execute, the Signing page shows a checklist built for your jurisdiction — things like how many witnesses you need and whether a notary is required.',
      'You work through each checklist item and confirm it with a non-certifying acknowledgement: you are confirming the step happened, not making a legal certification. When every item is attested, the checklist is marked complete and your firm can see it.',
    ],
  },
  {
    question: 'What are funding tasks?',
    answer: [
      'A plan only works if your assets are lined up behind it. Funding tasks are the concrete steps that connect your property to your plan — for example, retitling an account or updating a beneficiary designation.',
      'Each task moves from pending to in progress to complete, and you can attach evidence (like a confirmation document) as you go. Checkup flags any funding tasks that are still open.',
    ],
  },
  {
    question: 'What is the vault?',
    answer: [
      'The vault is where your final, released documents and your family letter are stored. From the Vault page you can create a share — a time-limited link that lets someone you trust view a redacted summary of your plan documents without signing in.',
      'Shares can be revoked at any time, and the person opening a share only ever sees the redacted summary, never your full documents.',
    ],
  },
];

const GLOSSARY: { term: string; definition: string }[] = [
  {
    term: 'Will',
    definition:
      'A document that says who receives your property, names guardians for minor children, and names an executor to carry out your wishes after you pass away.',
  },
  {
    term: 'Trust',
    definition:
      'An arrangement where a trustee holds and manages property for the benefit of others (the beneficiaries), following the rules you set out. A trust can avoid probate for the property placed in it.',
  },
  {
    term: 'Power of attorney',
    definition:
      'A document that lets someone you choose (your agent) make financial or legal decisions for you if you are unable to do so yourself.',
  },
  {
    term: 'Healthcare directive',
    definition:
      'A document that states your wishes for medical care and names a healthcare agent to make medical decisions for you if you cannot communicate them.',
  },
  {
    term: 'Guardian',
    definition:
      'The person you nominate to care for your minor children if you are unable to.',
  },
  {
    term: 'Executor',
    definition:
      'The person you nominate to carry out the instructions in your will and settle your affairs.',
  },
  {
    term: 'Trustee',
    definition:
      'The person or institution you nominate to manage property held in a trust for your beneficiaries.',
  },
  {
    term: 'Beneficiary',
    definition:
      'A person or organization you name to receive property or benefits from an asset, account, or trust.',
  },
];

function FaqItem({
  faq,
  open,
  onToggle,
  index,
}: {
  faq: Faq;
  open: boolean;
  onToggle: () => void;
  index: number;
}) {
  const buttonId = `faq-button-${index}`;
  const panelId = `faq-panel-${index}`;
  return (
    <div className="border-b border-lf-border last:border-b-0">
      <h3>
        <button
          type="button"
          id={buttonId}
          aria-expanded={open}
          aria-controls={panelId}
          onClick={onToggle}
          className="flex min-h-11 w-full items-center justify-between gap-3 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
        >
          <span className="text-sm font-semibold text-lf-text">{faq.question}</span>
          <ChevronDown
            className={cn('size-4 shrink-0 text-lf-text-muted transition-transform', open && 'rotate-180')}
            aria-hidden="true"
          />
        </button>
      </h3>
      {open && (
        <div id={panelId} role="region" aria-labelledby={buttonId} className="space-y-2 pb-4">
          {faq.answer.map((paragraph) => (
            <p key={paragraph.slice(0, 24)} className="text-sm text-lf-text-muted">
              {paragraph}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/** Help — FAQ, glossary of plan types and roles, and how to reach your firm. */
export default function FamilyHelpPage(): React.JSX.Element {
  const [openIndex, setOpenIndex] = useState<number | null>(0);

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Help</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          How LegacyFlow works, what the legal terms mean, and how to reach your firm. This page
          is general information — it is not legal advice.
        </p>
      </div>

      <section aria-label="Frequently asked questions">
        <Card>
          <CardHeader>
            <CardTitle>Frequently asked questions</CardTitle>
            <CardDescription>
              Short answers about intake, review, documents, signing, funding, and the vault.
            </CardDescription>
          </CardHeader>
          <CardContent className="px-4 sm:px-6">
            {FAQS.map((faq, index) => (
              <FaqItem
                key={faq.question}
                faq={faq}
                index={index}
                open={openIndex === index}
                onToggle={() => setOpenIndex(openIndex === index ? null : index)}
              />
            ))}
          </CardContent>
        </Card>
      </section>

      <section aria-label="Glossary">
        <Card>
          <CardHeader>
            <CardTitle>Glossary</CardTitle>
            <CardDescription>
              Plain-language definitions of the plan types and roles you will see in the app.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <dl className="divide-y divide-lf-border">
              {GLOSSARY.map((entry) => (
                <div key={entry.term} className="px-4 py-3 sm:px-6">
                  <dt className="text-sm font-semibold text-lf-text">{entry.term}</dt>
                  <dd className="mt-0.5 text-sm text-lf-text-muted">{entry.definition}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      </section>

      <section aria-label="Contact your firm" id="clarifications">
        <Card>
          <CardHeader>
            <CardTitle>Contact your firm</CardTitle>
            <CardDescription>
              How questions and clarifications flow between you and your attorney.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-lf-text-muted">
              When your attorney reviews your plan, they may post clarification questions on your
              matter — for example, asking for a missing detail or a document. Open questions from
              your firm appear as items in{' '}
              <Link
                href="/family/checkup"
                className="font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
              >
                Checkup
              </Link>
              , each linking to the page where you can provide what is needed.
            </p>
            <p className="text-sm text-lf-text-muted">
              The family workspace does not have in-app messaging, so for anything urgent or
              sensitive — changing your plan, updating your contact details, or questions about
              your firm&apos;s process — contact your firm directly using the phone number or
              email on your engagement paperwork.
            </p>
            <p
              role="note"
              className="flex items-start gap-2 rounded-lf-control border border-lf-accent-600 bg-lf-accent-50 px-4 py-3 text-sm text-lf-accent-800"
            >
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              If you believe something in your plan is wrong — a misspelled name, a wrong
              beneficiary, a role you want to change — tell your firm before you sign anything.
              Corrections are easiest before documents are released.
            </p>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
