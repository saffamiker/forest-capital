/**
 * document-generation-panel.test.tsx — the Generate Documents cards.
 *
 * Verifies the Executive Brief card persists to the editor: when the
 * generation endpoint returns an X-Draft-Id header, the card shows
 * [Open in Editor] (primary) and [Download] (secondary), and Open in
 * Editor navigates to /editor/{draft_id}.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import axios from 'axios'
import { AuthContext } from '../App'
import DocumentGenerationPanel from '../components/DocumentGenerationPanel'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('axios', () => {
  const fn = vi.fn()
  return { default: Object.assign(fn, { isAxiosError: vi.fn(() => false) }) }
})

const axiosMock = axios as unknown as ReturnType<typeof vi.fn>

const TEAM_PERMS = [
  'view_analytics', 'ask_council', 'team_member',
  'generate_documents', 'export_package',
]

function renderPanel(ui: ReactNode) {
  const value = {
    session: {
      token: 't', email: 'thaob@queens.edu', permissions: TEAM_PERMS,
    },
    isVerifying: false,
    login: vi.fn(),
    logout: vi.fn(),
  }
  return render(
    <AuthContext.Provider value={value}>{ui}</AuthContext.Provider>,
  )
}

/** A generation response carrying an editor draft id. */
function briefResponse(draftId: string) {
  return {
    data: new Blob(['docx-bytes']),
    headers: {
      'content-disposition':
        'attachment; filename="forest-capital-executive-brief.docx"',
      'content-type':
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'x-draft-id': draftId,
    },
  }
}

describe('DocumentGenerationPanel — Executive Brief editor persistence', () => {
  beforeEach(() => {
    mockNavigate.mockClear()
    axiosMock.mockReset()
    localStorage.clear()
  })

  it('shows Open in Editor and Download after the brief generates a draft', async () => {
    axiosMock.mockResolvedValue(briefResponse('4242'))
    renderPanel(<DocumentGenerationPanel />)

    const card = screen.getByText('Executive Brief')
      .closest('.card') as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: /Generate/ }))

    // The draft-backed result offers Open in Editor (primary) and
    // Download (secondary).
    await waitFor(() =>
      expect(within(card).getByText('Open in Editor')).toBeInTheDocument())
    expect(within(card).getByText('Download')).toBeInTheDocument()
  })

  it('Open in Editor navigates to /editor/{draft_id}', async () => {
    axiosMock.mockResolvedValue(briefResponse('4242'))
    renderPanel(<DocumentGenerationPanel />)

    const card = screen.getByText('Executive Brief')
      .closest('.card') as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: /Generate/ }))
    const openBtn = await within(card).findByText('Open in Editor')
    fireEvent.click(openBtn)

    expect(mockNavigate).toHaveBeenCalledWith('/editor/4242')
  })

  it('posts to the executive-brief generation endpoint', async () => {
    axiosMock.mockResolvedValue(briefResponse('7'))
    renderPanel(<DocumentGenerationPanel />)

    const card = screen.getByText('Executive Brief')
      .closest('.card') as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: /Generate/ }))

    await waitFor(() => expect(axiosMock).toHaveBeenCalled())
    expect(axiosMock.mock.calls[0][0]).toMatchObject({
      url: '/api/v1/export/executive-brief', method: 'POST',
    })
  })
})
