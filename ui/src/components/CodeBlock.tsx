import { useMemo } from "react";
import Prism from "prismjs";
import { redactText } from "../redact";
import "prismjs/components/prism-python";
import "prismjs/components/prism-json";
import "prismjs/components/prism-sql";
import "prismjs/components/prism-bash";
import "prismjs/components/prism-markup";
import "prismjs/components/prism-toml";
import "prismjs/components/prism-markdown";

/** Languages we highlight; anything else renders as plain text. */
const LANGS: Record<string, string> = {
  python: "python",
  py: "python",
  json: "json",
  sql: "sql",
  bash: "bash",
  sh: "bash",
  shell: "bash",
  toml: "toml",
  markdown: "markdown",
  md: "markdown",
};

const HIGHLIGHT_LIMIT = 60_000;

/** Highlight source for an overlay editor; null means render as plain text. */
export function highlightCode(text: string, language: string): string | null {
  const lang = LANGS[language.toLowerCase()];
  const grammar = lang ? Prism.languages[lang] : undefined;
  if (!lang || !grammar || text.length > HIGHLIGHT_LIMIT) return null;
  return Prism.highlight(text, grammar, lang);
}

/** Pretty-print a JSON string, or return it untouched when it is not
 *  complete JSON (e.g. a truncated result preview). */
export function formatJson(text: string): string {
  const trimmed = text.trim();
  if (!/^[[{]/.test(trimmed)) return text;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return text;
  }
}

export function looksLikeJson(text: string): boolean {
  return /^\s*[[{]/.test(text);
}

interface Props {
  code: string;
  /** Language hint (markdown fence tag or a fixed value). `auto` picks
   *  json when the text looks like JSON, else plain. */
  language?: string;
  /** Pretty-print JSON before highlighting (off for source code). */
  format?: boolean;
  /** Mask figures after formatting (`?redact=1` demos). */
  redact?: boolean;
  className?: string;
}

export default function CodeBlock({ code, language = "auto", format = false, redact = false, className }: Props) {
  const { html, lang, text } = useMemo(() => {
    let lang = LANGS[language.toLowerCase()];
    if (language === "auto" && looksLikeJson(code)) lang = "json";
    let text = format && lang === "json" ? formatJson(code) : code;
    if (redact) text = redactText(text);
    if (!lang || text.length > HIGHLIGHT_LIMIT) return { html: null, lang, text };
    return { html: Prism.highlight(text, Prism.languages[lang], lang), lang, text };
  }, [code, language, format, redact]);
  const cls = [className, lang ? `language-${lang}` : ""].filter(Boolean).join(" ");
  if (html === null) return <pre className={cls}>{text}</pre>;
  return <pre className={cls} dangerouslySetInnerHTML={{ __html: html }} />;
}
