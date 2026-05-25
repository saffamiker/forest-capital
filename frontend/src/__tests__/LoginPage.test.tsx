import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import axios from 'axios'
import LoginPage from '../pages/LoginPage'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

// Wrap LoginPage in MemoryRouter — required because LoginPage calls useSearchParams()
function renderLogin(initialPath = '/login') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <LoginPage />
    </MemoryRouter>
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ── Static rendering ────────────────────────────────────────────────────────

  it('renders without errors', () => {
    renderLogin()
    expect(document.body).toBeTruthy()
  })

  it('renders email input field', () => {
    renderLogin()
    expect(screen.getByRole('textbox')).toBeInTheDocument()
  })

  it('renders submit button', () => {
    renderLogin()
    expect(screen.getByRole('button', { name: /send magic link/i })).toBeInTheDocument()
  })

  it('submit button is disabled when email is empty', () => {
    renderLogin()
    const button = screen.getByRole('button', { name: /send magic link/i })
    expect(button).toBeDisabled()
  })

  it('submit button is enabled after typing a valid email', async () => {
    renderLogin()
    const input = screen.getByRole('textbox')
    await userEvent.type(input, 'ruurdsm@queens.edu')
    const button = screen.getByRole('button', { name: /send magic link/i })
    expect(button).not.toBeDisabled()
  })

  it('renders the Forest Capital institutional lockup image', () => {
    // May 24 2026 rebrand — the text wordmark was replaced by the
    // official hexagon-and-wordmark image. The alt text identifies
    // the brand for screen readers; the testid pins the surface.
    renderLogin()
    const lockup = screen.getByTestId('login-forest-capital-lockup')
    const img = lockup.querySelector('img')
    expect(img).toBeInTheDocument()
    expect(img?.getAttribute('alt')).toMatch(/forest capital/i)
    expect(img?.getAttribute('src')).toBe('/assets/logos/forest-capital.jpg')
  })

  it('renders the academic-context subtitle (FNA 670 + McColl)', () => {
    renderLogin()
    expect(screen.getByText(/McColl School of Business · FNA 670/i))
      .toBeInTheDocument()
  })

  it('renders the Queens + McColl institutional lockup row', () => {
    renderLogin()
    const row = screen.getByTestId('login-institutional-lockup')
    const imgs = row.querySelectorAll('img')
    // Two side-by-side institutional marks above the sign-in card.
    expect(imgs.length).toBe(2)
    const srcs = Array.from(imgs).map((i) => i.getAttribute('src'))
    expect(srcs).toContain('/assets/logos/queens.png')
    expect(srcs).toContain('/assets/logos/mccoll.jpeg')
    // Both alt texts identify the institution for screen readers.
    const alts = Array.from(imgs).map((i) => i.getAttribute('alt') ?? '')
    expect(alts.some((a) => /queens/i.test(a))).toBe(true)
    expect(alts.some((a) => /mccoll/i.test(a))).toBe(true)
  })

  it('renders the updated MSFA institutional footer', () => {
    // The footer carries the full attribution string per the
    // brand-uplift spec. A regression on this exact wording would
    // mis-attribute the platform — pin every component.
    renderLogin()
    const footer = screen.getByTestId('login-footer')
    expect(footer.textContent).toContain('MSFA FNA 670')
    expect(footer.textContent).toContain('Queens University of Charlotte')
    expect(footer.textContent).toContain('McColl School of Business')
  })

  // ── Approved email — status: "sent" ────────────────────────────────────────

  it('shows specific inbox confirmation for approved email', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: { status: 'sent', dev_mode: false } })
    renderLogin()
    await userEvent.type(screen.getByRole('textbox'), 'ruurdsm@queens.edu')
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => {
      expect(screen.getByText(/check your inbox/i)).toBeInTheDocument()
    })
  })

  it('shows the email address in the confirmation for approved email', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: { status: 'sent', dev_mode: false } })
    renderLogin()
    const email = 'thaob@queens.edu'
    await userEvent.type(screen.getByRole('textbox'), email)
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => {
      expect(screen.getByText(email)).toBeInTheDocument()
    })
  })

  it('allows returning to the form from the approved confirmation state', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: { status: 'sent', dev_mode: false } })
    renderLogin()
    await userEvent.type(screen.getByRole('textbox'), 'ruurdsm@queens.edu')
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => screen.getByText(/check your inbox/i))
    fireEvent.click(screen.getByText(/use a different email/i))
    expect(screen.getByRole('button', { name: /send magic link/i })).toBeInTheDocument()
  })

  it('shows dev mode note for approved email when dev_mode is true', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: { status: 'sent', dev_mode: true } })
    renderLogin()
    await userEvent.type(screen.getByRole('textbox'), 'ruurdsm@queens.edu')
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => {
      expect(screen.getByText(/dev mode/i)).toBeInTheDocument()
    })
  })

  // ── Unapproved email — status: "pending" ───────────────────────────────────

  it('shows generic confirmation for unapproved email', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: { status: 'pending', dev_mode: false } })
    renderLogin()
    await userEvent.type(screen.getByRole('textbox'), 'attacker@evil.com')
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => {
      expect(screen.getByText(/request received/i)).toBeInTheDocument()
    })
  })

  it('does not show the email address in the pending confirmation', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: { status: 'pending', dev_mode: false } })
    renderLogin()
    const email = 'attacker@evil.com'
    await userEvent.type(screen.getByRole('textbox'), email)
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => screen.getByText(/request received/i))
    expect(screen.queryByText(email)).not.toBeInTheDocument()
  })

  it('does not show dev mode note for pending (unapproved) email', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: { status: 'pending', dev_mode: true } })
    renderLogin()
    await userEvent.type(screen.getByRole('textbox'), 'nobody@evil.com')
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => screen.getByText(/request received/i))
    expect(screen.queryByText(/dev mode/i)).not.toBeInTheDocument()
  })

  // ── Error state ─────────────────────────────────────────────────────────────

  it('shows error message on failed submit', async () => {
    mockedAxios.post = vi.fn().mockRejectedValue(new Error('Network error'))
    mockedAxios.isAxiosError = vi.fn().mockReturnValue(false) as unknown as typeof mockedAxios.isAxiosError
    renderLogin()
    await userEvent.type(screen.getByRole('textbox'), 'ruurdsm@queens.edu')
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => {
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
    })
  })

  // ── Expired session banner ──────────────────────────────────────────────────

  it('shows session-expired banner when navigated from a 401 redirect', () => {
    renderLogin('/login?expired=1')
    expect(screen.getByText(/your session has expired/i)).toBeInTheDocument()
  })

  it('does not show session-expired banner on normal login page load', () => {
    renderLogin('/login')
    expect(screen.queryByText(/your session has expired/i)).not.toBeInTheDocument()
  })
})
