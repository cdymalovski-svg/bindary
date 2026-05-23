import jsPDF from 'jspdf';
import html2canvas from 'html2canvas';
import { getPageSize } from './pageSizes';

// Convert page-pixel size (96 DPI) to PDF points (72 DPI): pt = px * 72 / 96
const PX_TO_PT = 72 / 96;

export async function exportBookToPdf(book, pageRefs) {
  const { width, height } = getPageSize(book.page_size);
  const pdfW = width * PX_TO_PT;
  const pdfH = height * PX_TO_PT;

  const pdf = new jsPDF({
    unit: 'pt',
    format: [pdfW, pdfH],
    orientation: pdfW > pdfH ? 'landscape' : 'portrait',
  });

  for (let i = 0; i < pageRefs.length; i++) {
    const node = pageRefs[i];
    if (!node) continue;
    // eslint-disable-next-line no-await-in-loop
    const canvas = await html2canvas(node, {
      backgroundColor: '#F9F6F0',
      scale: 2,
      useCORS: true,
      allowTaint: false,
      logging: false,
    });
    const dataUrl = canvas.toDataURL('image/jpeg', 0.92);
    if (i > 0) pdf.addPage([pdfW, pdfH], pdfW > pdfH ? 'landscape' : 'portrait');
    pdf.addImage(dataUrl, 'JPEG', 0, 0, pdfW, pdfH, undefined, 'FAST');
  }

  const safe = (book.title || 'book').replace(/[^a-z0-9-_]+/gi, '_');
  pdf.save(`${safe}.pdf`);
}
