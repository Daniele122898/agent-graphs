import { tool } from "@opencode-ai/plugin"

// agent-graphs parallel-delegation bridge. Like ask_agent, but fans work out to
// SEVERAL teammates at once: it POSTs a list of (teammate, task) to our backend,
// which validates every target, runs them concurrently on their own persistent
// sessions, and returns the combined answers. Staged into <repo>/.opencode/tool/
// at runtime by the OpenCode harness (server.py); callback wiring (URL, token,
// our session id) comes from env vars the server manager injects; ctx.agent is
// our agent id (the asker).
export default tool({
  description:
    "Delegate to SEVERAL teammates AT ONCE, in parallel, and get all their answers back together. Use this to fan out independent work (e.g. a frontend task and a backend task simultaneously) instead of asking one teammate, waiting, then the next. Only the teammates listed in your instructions are reachable.",
  args: {
    assignments: tool.schema
      .array(
        tool.schema.object({
          target_id: tool.schema.string().describe("the teammate — their id (in backticks) or display name"),
          task: tool.schema.string().describe("the task or question for that teammate"),
        }),
      )
      .describe("one entry per teammate to delegate to (run concurrently)"),
  },
  async execute(args, ctx) {
    const base = process.env.AGENT_GRAPHS_CALLBACK_URL
    const token = process.env.AGENT_GRAPHS_CALLBACK_TOKEN ?? ""
    const sessionId = process.env.AGENT_GRAPHS_SESSION_ID ?? ""
    const res = await fetch(`${base}/internal/ask_team`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-ag-token": token },
      // timeout:false (Bun) — the backend owns the deadline; without this Bun's
      // ~255s fetch default kills this call mid-fan-out and orphans the target
      // runs ("The operation timed out").
      timeout: false,
      body: JSON.stringify({
        session_id: sessionId,
        asker_id: ctx.agent,
        assignments: args.assignments,
      }),
    } as RequestInit)
    const text = await res.text()
    if (!res.ok) throw new Error(text || `ask_team failed (${res.status})`)
    return text
  },
})
