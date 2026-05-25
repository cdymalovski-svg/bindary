import jsPDF from 'jspdf';
import html2canvas from 'html2canvas';
import { getPageSize } from './pageSizes';

// Convert page-pixel size (96 DPI) to PDF points (72 DPI): pt = px * 72 / 96
const PX_TO_PT = 72 / 96;

// Render up to this many pages with html2canvas simultaneously.
// Higher = faster on multi-core machines, but more memory pressure.
const CONCURRENCY = 3;

// Render one page node to a JPEG data URL. Extracted so we can run several
// in parallel without sequential awaits.
// Render one page node to a PNG data URL. PNG is lossless so colors are
// preserved exactly (no JPEG chroma compression). Extracted so we can run
// several renders in parallel without sequential awaits.
async function renderNodeToImage(node, scale) {
  const canvas = await html2canvas(node, {
    backgroundColor: '#F9F6F0',
    scale,
    // Reuse the DOM's already-loaded <img crossOrigin="anonymous"> elements
    // via drawImage() instead of re-fetching every asset via XHR. This is
    // dramatically faster (no second network round-trip per image) and is
    // still canvas-safe because every img tag is loaded with crossOrigin.
    useCORS: false,
    allowTaint: false,
    logging: false,
    foreignObjectRendering: false,
    removeContainer: true,
    imageTimeout: 15000,
  });
  return canvas.toDataURL('image/png');
}

export async function exportBookToPdf(book, pageRefs, { onProgress, scale = 2 } = {}) {
  const { width, height } = getPageSize(book.page_size);
  const pdfW = width * PX_TO_PT;
  const pdfH = height * PX_TO_PT;

  const pdf = new jsPDF({
    unit: 'pt',
    format: [pdfW, pdfH],
    orientation: pdfW > pdfH ? 'landscape' : 'portrait',
  });

  const validNodes = pageRefs.filter(Boolean);
  const total = validNodes.length;
  let completed = 0;
  const results = new Array(total);

  // Run renders in batches of CONCURRENCY to balance speed and memory.
  for (let start = 0; start < total; start += CONCURRENCY) {
    const batch = [];
    for (let j = 0; j < CONCURRENCY && start + j < total; j += 1) {
      const idx = start + j;
      batch.push(
        renderNodeToImage(validNodes[idx], scale).then((dataUrl) => {
          results[idx] = dataUrl;
          completed += 1;
          onProgress?.(completed, total);
        })
      );
    }
    // eslint-disable-next-line no-await-in-loop
    await Promise.all(batch);
  }

  // Add to the PDF in original page order.
  for (let i = 0; i < results.length; i += 1) {
    if (i > 0) pdf.addPage([pdfW, pdfH], pdfW > pdfH ? 'landscape' : 'portrait');
    pdf.addImage(results[i], 'PNG', 0, 0, pdfW, pdfH, undefined, 'FAST');
  }

  const safe = (book.title || 'book').replace(/[^a-z0-9-_]+/gi, '_');
  pdf.save(`${safe}.pdf`);
}
