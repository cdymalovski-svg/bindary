// Centralised HTML sanitisation for any user-authored content we render via
// `innerHTML` or `dangerouslySetInnerHTML`. Strips <script>, event handlers
// (onerror, onclick, ...), `javascript:` URLs, and other XSS-prone payloads
// while preserving the formatting tags the editor produces (b, i, u, p, br,
// span, em, strong, etc.).
import DOMPurify from 'dompurify';

const ALLOWED_TAGS = [
  'b', 'i', 'u', 'em', 'strong', 'p', 'br', 'span', 'div',
  'ul', 'ol', 'li', 'a',
];
const ALLOWED_ATTR = ['style', 'class', 'href', 'target', 'rel', 'data-placeholder', 'data-toc-target'];

export function sanitizeHtml(input) {
  if (input == null) return '';
  return DOMPurify.sanitize(String(input), {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    // Block javascript: URLs even inside otherwise-allowed href attributes.
    FORBID_ATTR: ['onerror', 'onclick', 'onload', 'onmouseover', 'onfocus'],
  });
}
