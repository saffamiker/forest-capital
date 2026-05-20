/**
 * speakerColours.ts — the frontend mirror of backend/tools/speaker_colours.py.
 *
 * Both surfaces resolve a speaker's colour from the same palette in the
 * same first-seen order, so the colour the editor shows for a speaker is
 * the colour the exported DOCX uses for them. Five distinct, accessible
 * colours; a sixth speaker cycles back to the first (in practice the
 * project team has three speakers).
 */

// Five colours, mirroring SPEAKER_COLOURS in backend/tools/speaker_colours.py.
export const SPEAKER_COLOURS = [
  '#1B2A4A', // navy   — Speaker 1
  '#B45309', // amber  — Speaker 2
  '#059669', // green  — Speaker 3
  '#7C3AED', // purple — Speaker 4
  '#DC2626', // red    — Speaker 5
] as const

/**
 * Resolves a speaker's stable colour from the document's full first-seen
 * speaker list. A name not present in allSpeakers maps to Speaker 1
 * (navy) — defensive fallback, never throws.
 */
export function getSpeakerColour(
  speakerName: string,
  allSpeakers: readonly string[],
): string {
  const idx = allSpeakers.indexOf(speakerName)
  const safe = idx < 0 ? 0 : idx
  // Non-null: SPEAKER_COLOURS is a constant tuple of length 5; modulo
  // its length always yields a defined entry.
  return SPEAKER_COLOURS[safe % SPEAKER_COLOURS.length]!
}
