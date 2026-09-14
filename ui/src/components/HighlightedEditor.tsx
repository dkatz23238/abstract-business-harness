import { useMemo, useRef, type KeyboardEvent } from "react";
import { highlightCode } from "./CodeBlock";

interface Props {
  value: string;
  onChange: (value: string) => void;
  language: "python" | "toml" | "markdown";
  wrap?: boolean;
  disabled?: boolean;
}

export default function HighlightedEditor({ value, onChange, language, wrap, disabled }: Props) {
  const preRef = useRef<HTMLPreElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const html = useMemo(() => highlightCode(value, language), [value, language]);
  const display = value.endsWith("\n") ? `${value}\n` : value;

  const sync = () => {
    const ta = taRef.current;
    const pre = preRef.current;
    if (!ta || !pre) return;
    pre.scrollTop = ta.scrollTop;
    pre.scrollLeft = ta.scrollLeft;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Tab") return;
    e.preventDefault();
    const ta = e.currentTarget;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const insert = "  ";
    onChange(value.slice(0, start) + insert + value.slice(end));
    requestAnimationFrame(() => {
      ta.selectionStart = ta.selectionEnd = start + insert.length;
    });
  };

  const shared =
    "m-0 box-border p-3.5 font-mono text-[13px] leading-[1.55] [tab-size:4] " +
    (wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre");

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden rounded-[10px] border border-line bg-code">
      <pre
        ref={preRef}
        aria-hidden
        className={`pointer-events-none absolute inset-0 overflow-hidden text-ink [&_code]:font-mono [&_code]:text-[13px] [&_code]:leading-[1.55] [&_code]:font-normal ${shared} language-${language}`}
      >
        {html ? (
          <code
            className={`language-${language}`}
            dangerouslySetInnerHTML={{ __html: html + (value.endsWith("\n") ? "\n" : "") }}
          />
        ) : (
          <code>{display}</code>
        )}
      </pre>
      <textarea
        ref={taRef}
        spellCheck={false}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onScroll={sync}
        onKeyDown={onKeyDown}
        className={`absolute inset-0 z-[1] resize-none overflow-auto border-0 bg-transparent text-transparent caret-ink outline-none selection:bg-accent/25 ${shared}`}
      />
    </div>
  );
}
