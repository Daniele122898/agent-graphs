import { tool } from "@opencode-ai/plugin"

// agent-graphs delegation bridge. Staged into <repo>/.opencode/tool/ at runtime
// by the OpenCode harness (server.py). It hands the call back to our backend,
// which enforces the neighbor/cycle/depth guards and runs the target agent on
// its own persistent session, then returns the answer. The callback wiring
// (URL, token, our session id) comes from env vars the server manager injects;
// ctx.agent is our agent id (OpenCode agents are named by our ids), so the
// asker is known without a session map.
export default tool({
  description:
    "Consult a teammate agent and get their concise answer. Use this when a question is outside your expertise; only the teammates listed in your instructions are reachable.",
  args: {
    target_id: tool.schema.string().describe("the teammate to ask — their id (in backticks) or display name; both resolve"),
    question: tool.schema.string().describe("the question to ask them"),
  },
  async execute(args, ctx) {
    const base = process.env.AGENT_GRAPHS_CALLBACK_URL
    const token = process.env.AGENT_GRAPHS_CALLBACK_TOKEN ?? ""
    const sessionId = process.env.AGENT_GRAPHS_SESSION_ID ?? ""
    const res = await fetch(`${base}/internal/ask_agent`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-ag-token": token },
      // timeout:false (Bun) — the backend owns the deadline; without this Bun's
      // ~255s fetch default kills this call mid-delegation and orphans the
      // target run ("The operation timed out").
      timeout: false,
      body: JSON.stringify({
        session_id: sessionId,
        asker_id: ctx.agent,
        target_id: args.target_id,
        question: args.question,
      }),
    } as RequestInit)
    const text = await res.text()
    if (!res.ok) throw new Error(text || `ask_agent failed (${res.status})`)
    return text
  },
})
