import { useRef, useState, type KeyboardEvent } from "react";

const SUGGESTIONS = [
  "QUARTERLY_REVENUE_TREND 2010→2014 // ANOMALY_DETECT",
  "VARIANCE_ANALYSIS: NET SALES DROP 2013 // -8.2%",
  "TOP_10_CUSTOMER_ENTITIES BY TOTAL_CREDIT_ALLOC",
  "MARGIN_DELTA: PRODUCT_CATEGORY // BIKES v ACCESSORIES",
  "TERRITORY_PERF_COMPARISON 2012 vs 2013 // DRIVER_ATTR",
  "CUSTOMER_SEGMENT_BREAKDOWN // EXEC_SUMMARY",
  "GROSS_PROFIT_ATTRIBUTION: REGION // CHANNEL",
  "MONTHLY_REVENUE LAST_12M // FORECAST_OVERLAY",
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
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  };

  return (
    <>
      {showSuggestions && (
        <div className="suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="chip" onClick={() => { setValue(s); onSend(s); }} title="Deploy quick interrogative">
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
          placeholder="ENTER INTERROGATIVE // e.g. 'QUARTERLY VARIANCE ANALYSIS Q4 2013 - DRIVER ATTRIBUTION' // アラサカ"
          rows={1}
          disabled={disabled}
        />
        <button className="send-btn" onClick={submit} disabled={disabled} title="Transmit to Gawain Core">
          {disabled ? "UPLINK..." : "EXECUTE ▶"}
        </button>
      </div>
    </>
  );
}
