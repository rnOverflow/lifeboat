import { Bot, Send, ShieldAlert, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Card, ErrorNote, inputCls } from "../components/ui";
import { api, type CopilotAction, type CopilotReply } from "../lib/api";
import { useAction } from "../lib/queries";

interface Msg { role: "user" | "assistant"; content: string; reply?: CopilotReply }

const SUGGESTIONS = ["What is happening right now?", "How ready are we for a failover?", "What's our current RPO exposure?", "Are our backups healthy?", "What should we do next?"];

/** Minimal, safe markdown: **bold** and line breaks only (no HTML injection). */
function Rich({ text }: { text: string }) {
  return (
    <div className="space-y-1 whitespace-pre-wrap">
      {text.split("\n").map((line, i) => (
        <div key={i}>{line.split(/(\*\*[^*]+\*\*)/g).map((part, j) => (part.startsWith("**") ? <strong key={j}>{part.slice(2, -2)}</strong> : part))}</div>
      ))}
    </div>
  );
}

function ActionButton({ a }: { a: CopilotAction }) {
  const [done, setDone] = useState<string | null>(null);
  const run = useAction(() => {
    const body = a.action.startsWith("initiate_") ? { reason: `Copilot suggestion: ${a.reason}`.slice(0, 480) } : a.action === "approve_run" ? { comment: "approved via copilot suggestion" } : {};
    return api(a.path.replace(/^\/api\/v1/, ""), { method: a.method, body });
  });
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-850 p-2.5 text-xs">
      <div className="flex items-center gap-2">
        <Badge tone="info">{a.action.replace(/_/g, " ")}</Badge>
        <span className="text-ink-400">{a.reason}</span>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <Button disabled={!a.permitted || run.isPending || !!done} onClick={() => run.mutate(undefined, { onSuccess: () => setDone("done") })}>
          {done ? "Executed" : "Run with my permissions"}
        </Button>
        {!a.permitted && <span className="text-warn">Your role cannot perform this action.</span>}
      </div>
      <div className="mt-1"><ErrorNote error={run.error} /></div>
    </div>
  );
}

export default function Copilot() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const ask = useAction((message: string) =>
    api<CopilotReply>("/copilot/chat", { method: "POST", body: { message, history: msgs.map(({ role, content }) => ({ role, content })) } }),
  );
  const [engine, setEngine] = useState<{ engine: string; model: string | null } | null>(null);
  useEffect(() => {
    api<{ engine: string; model: string | null }>("/copilot/info").then(setEngine).catch(() => setEngine(null));
  }, []);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);

  const send = (text: string) => {
    if (!text.trim() || ask.isPending) return;
    setMsgs((m) => [...m, { role: "user", content: text }]);
    setInput("");
    ask.mutate(text, { onSuccess: (r) => setMsgs((m) => [...m, { role: "assistant", content: r.answer, reply: r }]) });
  };

  return (
    <div className="mx-auto grid max-w-6xl gap-5 lg:grid-cols-[1fr_18rem]">
      <Card title="DR Copilot" action={engine && <Badge tone={engine.engine === "llm" ? "info" : "muted"}>{engine.engine === "llm" ? engine.model : "rule engine"}</Badge>}>
        <div className="flex h-[min(65vh,640px)] flex-col">
          <div className="flex-1 space-y-4 overflow-y-auto pr-1">
            {msgs.length === 0 && <div className="py-10 text-center text-sm text-ink-400">Ask about the live state of your DR posture. Answers come from real platform data.</div>}
            {msgs.map((m, i) => (
              <div key={i} className="flex gap-3">
                <div className={m.role === "user" ? "mt-0.5 text-ink-400" : "mt-0.5 text-azure"}>{m.role === "user" ? <User size={16} /> : <Bot size={16} />}</div>
                <div className="min-w-0 flex-1 text-sm">
                  <Rich text={m.content} />
                  {m.reply && (
                    <div className="mt-2 space-y-2">
                      {m.reply.suggested_actions.map((a, j) => <ActionButton key={j} a={a} />)}
                      <div className="text-[11px] text-ink-400">tools: {m.reply.tools_used.join(", ") || "none"}{m.reply.note && ` · ${m.reply.note}`}</div>
                    </div>
                  )}
                </div>
              </div>
            ))}
            {ask.isPending && <div className="text-sm text-ink-400">Thinking…</div>}
            <ErrorNote error={ask.error} />
            <div ref={end} />
          </div>
          <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <input className={inputCls} value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask the copilot…" maxLength={2000} aria-label="Message" />
            <Button variant="primary" type="submit" disabled={!input.trim() || ask.isPending} aria-label="Send"><Send size={15} /></Button>
          </form>
        </div>
      </Card>
      <div className="space-y-4">
        <Card title="Try">
          <div className="flex flex-col gap-1.5">
            {SUGGESTIONS.map((s) => <button key={s} onClick={() => send(s)} className="rounded-lg px-2 py-1.5 text-left text-sm text-ink-300 hover:bg-ink-800">{s}</button>)}
          </div>
        </Card>
        <Card title="Guardrails">
          <div className="flex gap-2 text-xs text-ink-400">
            <ShieldAlert size={28} className="shrink-0 text-warn" />
            <p>The copilot only has read-only tools, filtered by your role. It can propose actions but cannot run them. Anything it suggests goes through your own permissions, the approval gate and the audit log. Every question is audited.</p>
          </div>
        </Card>
      </div>
    </div>
  );
}
