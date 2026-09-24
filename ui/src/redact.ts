/** Display-only masking for sharing (`?redact=1`). Not a security control. */

import { useSyncExternalStore } from "react";

const YEAR = /^(?:19|20)\d{2}$/;
const TABLE = /^t[1-8]$/i;
const TOKEN = /\b(?:19|20)\d{2}\b|\bt[1-8]\b|\d[\d.,]*/gi;

let termPattern: RegExp | null = null;
let generation = 0;
const listeners = new Set<() => void>();

export function redactEnabled(): boolean {
  if (typeof window === "undefined") return false;
  const v = new URLSearchParams(window.location.search).get("redact");
  return v === "1" || v === "true" || v === "yes";
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Case-insensitive phrase match that does not fire inside a longer word. */
export function compileTermPattern(terms: readonly string[]): RegExp | null {
  const unique = new Map<string, string>();
  for (const raw of terms) {
    const term = raw.trim();
    if (!term) continue;
    unique.set(term.toLocaleLowerCase(), term);
  }
  const ordered = [...unique.values()].sort((a, b) => b.length - a.length);
  if (ordered.length === 0) return null;
  const body = ordered.map(escapeRegExp).join("|");
  return new RegExp(`(?<![\\p{L}\\p{N}])(?:${body})(?![\\p{L}\\p{N}])`, "giu");
}

/** Profile term list used while `?redact=1` is on. Safe to call before paint. */
export function setRedactTerms(terms: readonly string[]): void {
  termPattern = compileTermPattern(terms);
  generation += 1;
  for (const listener of listeners) listener();
}

export function useRedactGeneration(): number {
  return useSyncExternalStore(
    (onStoreChange) => {
      listeners.add(onStoreChange);
      return () => listeners.delete(onStoreChange);
    },
    () => generation,
    () => 0,
  );
}

function maskTerm(match: string): string {
  return match.replace(/[^\s]/g, "█");
}

/** Replace configured names, then digit runs, with a mask. Leaves 19xx/20xx years and t1–t8. */
export function redactText(text: string): string {
  const named = termPattern ? text.replace(termPattern, maskTerm) : text;
  return named.replace(TOKEN, (m) => (YEAR.test(m) || TABLE.test(m) ? m : m.replace(/\d/g, "X")));
}

export function maybeRedact(text: string): string {
  return redactEnabled() ? redactText(text) : text;
}

const SKIP_TAGS = new Set(["STYLE", "SCRIPT", "NOSCRIPT", "TEXTAREA"]);

/** Mask numbers and configured names in HTML text nodes only, so CSS and tags stay intact. */
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
