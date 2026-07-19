import { useRef, useState, type KeyboardEvent } from "react";

const SUGGESTIONS = [
  "Why did sales drop in 2013?",
  "Which products sell most in each region?",
  "Revenue trend by category 2010–2014",
  "Gross profit margin by product category",
  "Top 10 customers by total spend",
  "Compare territories: 2012 vs 2013",
  "Customer segment breakdown",
  "Monthly revenue last 12 months",
];

interface Props {
  onSend: (q: string) => void;
  disabled: boolean;
  showSuggestions: boolean;
}

export function ChatInput({ onSend, disabled, showSuggestions }: Props) {
  const [value, setValue]   = useState("");
  const textareaRef         = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const q = value.trim();
    if (!q || disabled) return;
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    onSend(q);
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
  };

  const onInput = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 150) + "px";
  };

  return (
    <>
      {showSuggestions && (
        <div className="suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="chip" onClick={() => { setValue(s); onSend(s); }}>
              {s}
            </button>
          ))}
        </div>
      )}
      <div className="input-area">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onInput={onInput}
          onKeyDown={onKey}
          placeholder="Ask anything about your data — e.g. 'Why did Bikes revenue fall in 2013?'"
          rows={1}
          disabled={disabled}
        />
        <button className="send-btn" onClick={submit} disabled={disabled}>Ask</button>
      </div>
    </>
  );
}
