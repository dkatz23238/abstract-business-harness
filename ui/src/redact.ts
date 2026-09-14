/** Display-only masking of figures for demos (`?redact=1`). Not a security control. */

const YEAR = /^(?:19|20)\d{2}$/;
const TABLE = /^t[1-8]$/i;
const TOKEN = /\b(?:19|20)\d{2}\b|\bt[1-8]\b|\d[\d.,]*/gi;

export function redactEnabled(): boolean {
  if (typeof window === "undefined") return false;
  const v = new URLSearchParams(window.location.search).get("redact");
  return v === "1" || v === "true" || v === "yes";
}

/** Replace digit runs with X, keeping separators. Leaves 19xx/20xx years and t1–t8. */
export function redactText(text: string): string {
  return text.replace(TOKEN, (m) => (YEAR.test(m) || TABLE.test(m) ? m : m.replace(/\d/g, "X")));
}

export function maybeRedact(text: string): string {
  return redactEnabled() ? redactText(text) : text;
}

const SKIP_TAGS = new Set(["STYLE", "SCRIPT", "NOSCRIPT", "TEXTAREA"]);

/** Mask numbers in HTML text nodes only, so CSS and tags stay intact. */
export function redactHtml(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const parent = node.parentElement;
      if (parent && SKIP_TAGS.has(parent.tagName)) return;
      node.textContent = redactText(node.textContent ?? "");
      return;
    }
    for (const child of Array.from(node.childNodes)) walk(child);
  };
  walk(doc);
  const out = doc.documentElement?.outerHTML ?? html;
  return doc.doctype ? `<!doctype html>\n${out}` : out;
}
