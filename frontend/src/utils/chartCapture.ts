/**
 * chartCapture.ts — capture a chart or table DOM node as a print-quality
 * PNG for the academic export package.
 *
 * Nodes are captured at 2× resolution on a white background. The export
 * renderer mounts the charts already light-themed (LIGHT_CHART_THEME),
 * so capture itself does no theming — it just rasterises the node.
 *
 * API note: the original spec proposed captureChart(elementId: string),
 * but the chart components carry no DOM `id`s. captureElement() takes the
 * node directly — the off-screen export renderer holds a ref to each.
 */

/** Capture a DOM node as a 2× PNG blob on a white background. */
export async function captureElement(node: HTMLElement): Promise<Blob> {
  // html2canvas is loaded lazily so it is not in the main bundle.
  const { default: html2canvas } = await import('html2canvas')
  const canvas = await html2canvas(node, {
    scale: 2,                 // 2× for print / Word-embed quality
    backgroundColor: '#FFFFFF',
    useCORS: true,
    logging: false,
  })
  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('canvas.toBlob returned null'))),
      'image/png',
    )
  })
}

/**
 * A white PNG placeholder used when a chart capture fails — keeps the
 * export package complete (one failed chart never fails the whole ZIP).
 */
export function placeholderImage(label: string): Blob {
  const w = 800
  const h = 480
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.fillStyle = '#FFFFFF'
    ctx.fillRect(0, 0, w, h)
    ctx.strokeStyle = '#E2E8F0'
    ctx.strokeRect(8, 8, w - 16, h - 16)
    ctx.fillStyle = '#1A1A2E'
    ctx.font = 'bold 22px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText(label, w / 2, h / 2 - 16)
    ctx.fillStyle = '#4A4A6A'
    ctx.font = '16px sans-serif'
    ctx.fillText('Capture failed — export manually via CSV', w / 2, h / 2 + 18)
  }
  // toDataURL is synchronous; convert the base64 PNG to a Blob.
  const dataUrl = canvas.toDataURL('image/png')
  const byteString = atob(dataUrl.split(',')[1])
  const bytes = new Uint8Array(byteString.length)
  for (let i = 0; i < byteString.length; i++) bytes[i] = byteString.charCodeAt(i)
  return new Blob([bytes], { type: 'image/png' })
}
