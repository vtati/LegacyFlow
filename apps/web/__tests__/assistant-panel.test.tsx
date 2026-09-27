/** @vitest-environment jsdom */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AssistantPanel, AI_DISCLAIMER } from '../components/ai/assistant-panel';
import type { AiAskResponse } from '../lib/api-client';

const answered: AiAskResponse = {
  answer: 'Probate is the court process for settling an estate.',
  citations: [{ pack: 'ca-probate-basics', section: 'what-is-probate' }],
  refused: false,
  safe_message: null,
};

const refused: AiAskResponse = {
  answer: '',
  citations: [],
  refused: true,
  safe_message: 'I can’t give legal advice on that.',
};

describe('AssistantPanel', () => {
  it('shows the disclaimer and asks via the provided ask function', async () => {
    const user = userEvent.setup();
    const ask = vi.fn().mockResolvedValue(answered);
    render(<AssistantPanel ask={ask} />);

    expect(screen.getByText(AI_DISCLAIMER)).toBeDefined();

    await user.type(screen.getByLabelText('Ask a question'), 'What is probate?');
    await user.click(screen.getByRole('button', { name: 'Send question' }));

    await waitFor(() => {
      expect(ask).toHaveBeenCalledWith('What is probate?');
    });
    expect(screen.getByText(/Probate is the court process/)).toBeDefined();
    expect(screen.getByText('ca-probate-basics · what-is-probate')).toBeDefined();
  });

  it('renders refused questions with the safe message and the attorney prompt', async () => {
    const user = userEvent.setup();
    const ask = vi.fn().mockResolvedValue(refused);
    render(<AssistantPanel ask={ask} />);

    await user.type(screen.getByLabelText('Ask a question'), 'Should I disinherit my child?');
    await user.click(screen.getByRole('button', { name: 'Send question' }));

    await waitFor(() => {
      expect(screen.getByText('I can’t give legal advice on that.')).toBeDefined();
    });
    expect(screen.getByText(/Ask your attorney/)).toBeDefined();
  });

  it('disables the send button for empty questions', async () => {
    render(<AssistantPanel ask={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Send question' })).toHaveProperty('disabled', true);
  });
});
