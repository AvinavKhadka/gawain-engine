import { useState, useCallback, useRef } from "react";
import type { Message, Block, GridData, ChartData, KpiCard } from "../types";

let msgId = 0;

export function useChat() {
  const [messages, setMessages]   = useState<Message[]>([]);
  const [busy, setBusy]           = useState(false);
  const sessionIdRef              = useRef<string>(crypto.randomUUID());
  const abortRef                  = useRef<AbortController | null>(null);

  const newChat = useCallback(() => {
    sessionIdRef.current = crypto.randomUUID();
    setMessages([]);
    setBusy(false);
    abortRef.current?.abort();
  }, []);

  const addUserMessage = useCallback((question: string) => {
    setMessages((prev) => [
      ...prev,
      { id: ++msgId, role: "user", blocks: [{ kind: "answer", text: question }], streaming: false },
    ]);
  }, []);

  const _streamIntoMessage = useCallback(
    async (endpoint: string, body: object, questionLabel?: string) => {
      if (busy) return;
      setBusy(true);
      if (questionLabel) addUserMessage(questionLabel);

      const assistantId = ++msgId;
      setMessages((prev) => [
        ...prev,
        { id: assistantId, role: "assistant", blocks: [{ kind: "step", text: "Thinking…" }], streaming: true },
      ]);

      const updateBlocks = (updater: (b: Block[]) => Block[]) => {
        setMessages((prev) =>
          prev.map((m) => m.id === assistantId ? { ...m, blocks: updater(m.blocks) } : m)
        );
      };

      abortRef.current = new AbortController();
      try {
        const resp = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: abortRef.current.signal,
        });

        const reader  = resp.body!.getReader();
        const decoder = new TextDecoder();
        let buf = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop() ?? "";

          for (const line of lines) {
            if (!line.trim()) continue;
            let ev: { type: string; content: unknown };
            try { ev = JSON.parse(line); } catch { continue; }

            switch (ev.type) {
              case "session":
                // Server echoes back the session id (or assigns a new one)
                if (typeof ev.content === "string") sessionIdRef.current = ev.content;
                break;

              case "step":
                updateBlocks((blocks) => {
                  const last = blocks[blocks.length - 1];
                  if (last?.kind === "step")
                    return [...blocks.slice(0, -1), { kind: "step", text: ev.content as string }];
                  return [...blocks, { kind: "step", text: ev.content as string }];
                });
                break;

              case "sql":
                updateBlocks((blocks) =>
                  blocks.filter((b) => b.kind !== "step")
                        .concat({ kind: "sql", sql: ev.content as string })
                );
                break;

              case "kpi":
                updateBlocks((blocks) =>
                  blocks.filter((b) => b.kind !== "step")
                        .concat({ kind: "kpi", cards: ev.content as KpiCard[] })
                );
                break;

              case "grid":
                updateBlocks((blocks) =>
                  blocks.filter((b) => b.kind !== "step")
                        .concat({ kind: "grid", data: ev.content as GridData })
                );
                break;

              case "chart":
                updateBlocks((blocks) =>
                  blocks.filter((b) => b.kind !== "step")
                        .concat({ kind: "chart", data: ev.content as ChartData })
                );
                break;

              case "token":
                updateBlocks((blocks) => {
                  // Remove any lingering step before the analysis starts
                  const noSteps = blocks.filter((b) => b.kind !== "step");
                  const last = noSteps[noSteps.length - 1];
                  if (last?.kind === "answer")
                    return [...noSteps.slice(0, -1),
                            { kind: "answer", text: last.text + (ev.content as string) }];
                  return [...noSteps, { kind: "answer", text: ev.content as string }];
                });
                break;

              case "error":
                updateBlocks((blocks) =>
                  blocks.filter((b) => b.kind !== "step")
                        .concat({ kind: "error", text: ev.content as string })
                );
                break;

              case "done":
                setMessages((prev) =>
                  prev.map((m) => m.id === assistantId ? { ...m, streaming: false } : m)
                );
                break;
            }
          }
        }
      } catch (err: unknown) {
        if ((err as Error).name !== "AbortError") {
          updateBlocks((blocks) =>
            blocks.concat({ kind: "error", text: (err as Error).message })
          );
        }
        setMessages((prev) =>
          prev.map((m) => m.id === assistantId ? { ...m, streaming: false } : m)
        );
      }
      setBusy(false);
    },
    [busy, addUserMessage]
  );

  const send = useCallback(
    (question: string) => _streamIntoMessage(
      "/api/chat",
      { question, session_id: sessionIdRef.current },
      question,
    ),
    [_streamIntoMessage]
  );

  const runSql = useCallback(
    (sql: string) => _streamIntoMessage(
      "/api/chat/run-sql",
      { sql, session_id: sessionIdRef.current },
    ),
    [_streamIntoMessage]
  );

  return { messages, busy, send, runSql, newChat, sessionId: sessionIdRef.current };
}
