import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axios from 'axios'
import LoginPage from '../pages/LoginPage'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders without errors', () => {
    render(<LoginPage />)
    expect(document.body).toBeTruthy()
  })

  it('renders email input field', () => {
    render(<LoginPage />)
    expect(screen.getByRole('textbox')).toBeInTheDocument()
  })

  it('renders submit button', () => {
    render(<LoginPage />)
    expect(screen.getByRole('button', { name: /send magic link/i })).toBeInTheDocument()
  })

  it('submit button is disabled when email is empty', () => {
    render(<LoginPage />)
    const button = screen.getByRole('button', { name: /send magic link/i })
    expect(button).toBeDisabled()
  })

  it('submit button is enabled after typing a valid email', async () => {
    render(<LoginPage />)
    const input = screen.getByRole('textbox')
    await userEvent.type(input, 'ruurdsm@queens.edu')
    const button = screen.getByRole('button', { name: /send magic link/i })
    expect(button).not.toBeDisabled()
  })

  it('shows confirmation message after successful submit', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
    render(<LoginPage />)
    const input = screen.getByRole('textbox')
    await userEvent.type(input, 'ruurdsm@queens.edu')
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => {
      expect(screen.getByText(/check your inbox/i)).toBeInTheDocument()
    })
  })

  it('shows the email in the confirmation message', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
    render(<LoginPage />)
    const email = 'thaob@queens.edu'
    await userEvent.type(screen.getByRole('textbox'), email)
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => {
      expect(screen.getByText(email)).toBeInTheDocument()
    })
  })

  it('allows returning to the form from the confirmation state', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
    render(<LoginPage />)
    await userEvent.type(screen.getByRole('textbox'), 'ruurdsm@queens.edu')
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => screen.getByText(/check your inbox/i))
    fireEvent.click(screen.getByText(/use a different email/i))
    expect(screen.getByRole('button', { name: /send magic link/i })).toBeInTheDocument()
  })

  it('shows error message on failed submit', async () => {
    mockedAxios.post = vi.fn().mockRejectedValue(new Error('Network error'))
    mockedAxios.isAxiosError = vi.fn().mockReturnValue(false) as unknown as typeof mockedAxios.isAxiosError
    render(<LoginPage />)
    await userEvent.type(screen.getByRole('textbox'), 'ruurdsm@queens.edu')
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => {
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
    })
  })

  it('shows dev mode note when dev_mode is true in response', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: { dev_mode: true } })
    render(<LoginPage />)
    await userEvent.type(screen.getByRole('textbox'), 'ruurdsm@queens.edu')
    fireEvent.click(screen.getByRole('button', { name: /send magic link/i }))
    await waitFor(() => {
      expect(screen.getByText(/dev mode/i)).toBeInTheDocument()
    })
  })

  it('renders institution branding', () => {
    render(<LoginPage />)
    expect(screen.getAllByText(/forest capital/i).length).toBeGreaterThan(0)
  })

  it('renders MSFA practicum footer', () => {
    render(<LoginPage />)
    expect(screen.getByText(/MSFA FNA 667/)).toBeInTheDocument()
  })
})
