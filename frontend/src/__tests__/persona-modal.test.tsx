/**
 * persona-modal.test.tsx
 *
 * Tests for the Council Persona Modal (Explainer §2.2):
 *   1. Fetches /api/agents/personas on mount and finds the right agent.
 *   2. Three tabs: Prompt (verbatim), Plain English (Explainer-generated),
 *      This Session (current council contribution).
 *   3. Plain English tab fires loadPersona from glossaryStore once per
 *      agent (cached on re-open).
 *   4. Close affordances: X button + Escape key + backdrop click.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axios from 'axios'

import PersonaModal from '../components/PersonaModal'
import { useGlossaryStore } from '../stores/glossaryStore'


vi.mock('axios')
const mockedAxios = axios as unknown as {
  get:  ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  isAxiosError: typeof axios.isAxiosError
}


const PERSONAS_FIXTURE = {
  agents: [
    {
      agent: 'Equity Analyst',
      model: 'claude-sonnet-4-6',
      module: 'agents.equity_analyst',
      system_prompt: 'You are a quantitative equity analyst. You analyse equity market conditions and momentum signals.',
      prompt_summary_first_sentence: 'You are a quantitative equity analyst.',
    },
    {
      agent: 'CIO',
      model: 'claude-opus-4-7',
      module: 'agents.cio',
      system_prompt: 'You are the Chief Investment Officer.',
      prompt_summary_first_sentence: 'You are the Chief Investment Officer.',
    },
  ],
}


beforeEach(() => {
  useGlossaryStore.setState({
    terms: {}, parameters: {}, personas: {}, qa: {}, charts: {},
    termsLoaded: false, termsLoading: false, inflight: new Set<string>(),
  })
  mockedAxios.get  = vi.fn().mockResolvedValue({ data: PERSONAS_FIXTURE })
  mockedAxios.post = vi.fn().mockResolvedValue({
    data: {
      plain_english:     'Plain English explanation of what the equity analyst does.',
      design_decisions:  'Design decisions behind the equity-analyst prompt.',
      this_session:      'In this session, the equity analyst flagged momentum.',
    },
  })
  mockedAxios.isAxiosError = (() => false) as never
})

afterEach(() => {
  vi.clearAllMocks()
})


describe('PersonaModal — fetch and tabs', () => {
  it('fetches personas on mount', async () => {
    render(<PersonaModal agentName="Equity Analyst" onClose={() => undefined} />)
    expect(mockedAxios.get).toHaveBeenCalledWith('/api/agents/personas')
  })

  it('renders all three tabs', async () => {
    render(<PersonaModal agentName="Equity Analyst" onClose={() => undefined} />)
    await screen.findByTestId('persona-tab-prompt')
    expect(screen.getByTestId('persona-tab-plain')).toBeInTheDocument()
    expect(screen.getByTestId('persona-tab-session')).toBeInTheDocument()
  })

  it('Prompt tab shows verbatim system_prompt for the right agent', async () => {
    render(<PersonaModal agentName="Equity Analyst" onClose={() => undefined} />)
    await screen.findByTestId('persona-tab-content-prompt')
    expect(screen.getByText(/quantitative equity analyst/)).toBeInTheDocument()
    // CIO prompt should not appear — wrong agent was requested.
    expect(screen.queryByText(/Chief Investment Officer/)).not.toBeInTheDocument()
  })

  it('Plain English tab fires loadPersona once per agent', async () => {
    const user = userEvent.setup()
    render(<PersonaModal agentName="Equity Analyst" onClose={() => undefined} />)
    await screen.findByTestId('persona-tab-prompt')
    await user.click(screen.getByTestId('persona-tab-plain'))
    // loadPersona → POST /api/explain/persona. The axios.post mock above
    // resolves immediately so the narrative renders.
    await screen.findByTestId('persona-tab-content-plain')
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/explain/persona',
      expect.objectContaining({ agent_name: 'Equity Analyst' }),
    )
  })

  it('Plain English caches — re-opening same agent does not re-fire load', async () => {
    // Pre-populate the cache.
    useGlossaryStore.setState({
      personas: {
        'Equity Analyst': {
          plain_english:    'Cached plain English.',
          design_decisions: 'Cached design decisions.',
          this_session:     'Cached this session.',
        },
      },
    })
    const user = userEvent.setup()
    render(<PersonaModal agentName="Equity Analyst" onClose={() => undefined} />)
    await screen.findByTestId('persona-tab-prompt')
    await user.click(screen.getByTestId('persona-tab-plain'))
    expect(screen.getByText('Cached plain English.')).toBeInTheDocument()
    // POST should NOT have fired — the cached entry guards the request.
    expect(mockedAxios.post).not.toHaveBeenCalled()
  })

  it('This Session tab falls back to passed sessionContent when no glossary entry', async () => {
    // For this test, make the Explainer call reject so plainEnglish stays
    // empty — that's the path where sessionContent fallback fires.
    mockedAxios.post = vi.fn().mockRejectedValue(new Error('explainer offline'))

    const user = userEvent.setup()
    render(
      <PersonaModal
        agentName="Equity Analyst"
        sessionContent="Equity momentum is strongly positive this run."
        onClose={() => undefined}
      />,
    )
    await screen.findByTestId('persona-tab-prompt')
    await user.click(screen.getByTestId('persona-tab-session'))
    expect(await screen.findByTestId('persona-tab-content-session')).toBeInTheDocument()
    expect(screen.getByText(/momentum is strongly positive/)).toBeInTheDocument()
  })
})


describe('PersonaModal — close affordances', () => {
  it('Close button fires onClose', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<PersonaModal agentName="Equity Analyst" onClose={onClose} />)
    await user.click(screen.getByLabelText('Close persona modal'))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('Escape key fires onClose', async () => {
    const onClose = vi.fn()
    render(<PersonaModal agentName="Equity Analyst" onClose={onClose} />)
    await screen.findByTestId('persona-modal')
    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' })
    })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('Backdrop click fires onClose', async () => {
    const onClose = vi.fn()
    render(<PersonaModal agentName="Equity Analyst" onClose={onClose} />)
    const modal = await screen.findByTestId('persona-modal')
    // Click on the backdrop (the modal container itself, not its child).
    fireEvent.click(modal)
    expect(onClose).toHaveBeenCalledOnce()
  })
})
