'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import {
  attorneyApi,
  listPublicFirms,
  ApiError,
  type AttorneyApplicationCreate,
  type FirmProfile,
} from '@/lib/api-client';

const JURISDICTIONS = [
  { code: 'US-VA', label: 'Virginia' },
  { code: 'US-MD', label: 'Maryland' },
  { code: 'US-DC', label: 'District of Columbia' },
  { code: 'US-NC', label: 'North Carolina' },
] as const;

/**
 * Attorney application form — manual onboarding.
 * Attorneys provide credentials; a firm admin verifies manually.
 */
export function AttorneyApplyForm(): React.JSX.Element {
  const router = useRouter();
  const [firms, setFirms] = useState<FirmProfile[]>([]);
  const [loadingFirms, setLoadingFirms] = useState(true);
  const [firmsError, setFirmsError] = useState<unknown>(null);

  const [firmId, setFirmId] = useState('');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [barNumber, setBarNumber] = useState('');
  const [jurisdictions, setJurisdictions] = useState<string[]>(['US-VA']);
  const [yearsExperience, setYearsExperience] = useState('');
  const [practiceAreas, setPracticeAreas] = useState('');
  const [barCardUrl, setBarCardUrl] = useState('');
  const [insuranceUrl, setInsuranceUrl] = useState('');
  const [idUrl, setIdUrl] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    listPublicFirms()
      .then((result) => {
        setFirms(result);
        if (result.length === 1) setFirmId(result[0].id);
      })
      .catch((err: unknown) => setFirmsError(err))
      .finally(() => setLoadingFirms(false));
  }, []);

  const toggleJurisdiction = (code: string): void => {
    setJurisdictions((prev) =>
      prev.includes(code) ? prev.filter((j) => j !== code) : [...prev, code],
    );
  };

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError(null);

    if (!firmId) {
      setError('Select the firm you are applying to.');
      return;
    }
    if (jurisdictions.length === 0) {
      setError('Select at least one jurisdiction where you are licensed.');
      return;
    }

    setSubmitting(true);
    try {
      const input: AttorneyApplicationCreate = {
        firm_id: firmId,
        full_name: fullName.trim(),
        email: email.trim(),
        phone: phone.trim() || null,
        bar_number: barNumber.trim(),
        jurisdictions,
        years_experience: yearsExperience ? parseInt(yearsExperience, 10) : null,
        practice_areas: practiceAreas.trim() || null,
        document_urls: {
          ...(barCardUrl.trim() ? { bar_card: barCardUrl.trim() } : {}),
          ...(insuranceUrl.trim() ? { insurance: insuranceUrl.trim() } : {}),
          ...(idUrl.trim() ? { id_document: idUrl.trim() } : {}),
        },
      };
      await attorneyApi.submitApplication(input);
      setSuccess(true);
    } catch (err: unknown) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not submit your application. Please try again.',
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (loadingFirms) return <LoadingState lines={4} label="Loading…" />;
  if (firmsError != null)
    return <ErrorState error={firmsError} onRetry={() => window.location.reload()} />;

  if (success) {
    return (
      <Card>
        <CardContent className="space-y-4 p-6 text-center">
          <h2 className="text-xl font-semibold text-lf-text">Application submitted</h2>
          <p className="text-sm leading-relaxed text-lf-text-muted">
            Thank you. Our team will verify your bar credentials and documents manually.
            We&rsquo;ll reach out at <span className="font-medium text-lf-text">{email}</span> once
            your application is reviewed.
          </p>
          <Button type="button" variant="outline" onClick={() => router.push('/')}>
            Back to home
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-6">
        <form onSubmit={handleSubmit} className="space-y-5">
          {firms.length === 0 ? (
            <EmptyState
              title="No firms available"
              description="There are no firms accepting applications right now."
            />
          ) : (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="attorney-firm">Firm</Label>
                <select
                  id="attorney-firm"
                  value={firmId}
                  onChange={(e) => setFirmId(e.target.value)}
                  required
                  className="flex w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
                >
                  <option value="">Select a firm…</option>
                  {firms.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.display_name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid gap-5 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="attorney-name">Full name</Label>
                  <Input
                    id="attorney-name"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    required
                    maxLength={200}
                    placeholder="Jordan Ellis, Esq."
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="attorney-email">Email</Label>
                  <Input
                    id="attorney-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    maxLength={320}
                    placeholder="you@firm.com"
                  />
                </div>
              </div>

              <div className="grid gap-5 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="attorney-phone">Phone (optional)</Label>
                  <Input
                    id="attorney-phone"
                    type="tel"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    maxLength={40}
                    placeholder="(555) 123-4567"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="attorney-bar">Bar number</Label>
                  <Input
                    id="attorney-bar"
                    value={barNumber}
                    onChange={(e) => setBarNumber(e.target.value)}
                    required
                    maxLength={100}
                    placeholder="e.g. 12345"
                  />
                </div>
              </div>

              <fieldset className="space-y-2">
                <legend className="text-sm font-medium text-lf-text">
                  Jurisdictions licensed
                </legend>
                <div className="flex flex-wrap gap-2">
                  {JURISDICTIONS.map((j) => (
                    <label
                      key={j.code}
                      className={`lf-pressable cursor-pointer rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ${
                        jurisdictions.includes(j.code)
                          ? 'border-lf-primary-600 bg-lf-primary-600 text-white'
                          : 'border-lf-border bg-white text-lf-text-muted hover:border-lf-primary-400'
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="sr-only"
                        checked={jurisdictions.includes(j.code)}
                        onChange={() => toggleJurisdiction(j.code)}
                      />
                      {j.label}
                    </label>
                  ))}
                </div>
              </fieldset>

              <div className="grid gap-5 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="attorney-exp">Years of experience (optional)</Label>
                  <Input
                    id="attorney-exp"
                    type="number"
                    min={0}
                    max={80}
                    value={yearsExperience}
                    onChange={(e) => setYearsExperience(e.target.value)}
                    placeholder="10"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="attorney-areas">Practice areas (optional)</Label>
                  <Input
                    id="attorney-areas"
                    value={practiceAreas}
                    onChange={(e) => setPracticeAreas(e.target.value)}
                    maxLength={2000}
                    placeholder="Estate planning, probate"
                  />
                </div>
              </div>

              <div className="space-y-4 rounded-lf-control border border-lf-border bg-lf-canvas-subtle p-4">
                <p className="text-sm font-medium text-lf-text">Supporting documents</p>
                <p className="text-xs leading-relaxed text-lf-text-muted">
                  Provide links to your documents (upload them to your firm&rsquo;s secure
                  storage and paste the links here), or leave blank and send them to the
                  firm directly after applying.
                </p>
                <div className="space-y-1.5">
                  <Label htmlFor="attorney-barcard">Bar card (link)</Label>
                  <Input
                    id="attorney-barcard"
                    type="url"
                    value={barCardUrl}
                    onChange={(e) => setBarCardUrl(e.target.value)}
                    placeholder="https://…"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="attorney-insurance">Malpractice insurance (link)</Label>
                  <Input
                    id="attorney-insurance"
                    type="url"
                    value={insuranceUrl}
                    onChange={(e) => setInsuranceUrl(e.target.value)}
                    placeholder="https://…"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="attorney-id">Government ID (link)</Label>
                  <Input
                    id="attorney-id"
                    type="url"
                    value={idUrl}
                    onChange={(e) => setIdUrl(e.target.value)}
                    placeholder="https://…"
                  />
                </div>
              </div>

              {error && (
                <p role="alert" className="text-sm text-lf-error-700">
                  {error}
                </p>
              )}

              <Button type="submit" disabled={submitting} className="w-full sm:w-auto">
                {submitting ? 'Submitting…' : 'Submit application'}
              </Button>
            </>
          )}
        </form>
      </CardContent>
    </Card>
  );
}
