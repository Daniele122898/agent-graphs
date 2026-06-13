import { tool } from "@opencode-ai/plugin"

// Smoke test of the delegation callback mechanism: a custom tool that calls
// back into our (stubbed) Python backend, passing the calling session + agent
// identity. In the real integration this is where neighbor/cycle/depth guards
// live and where the target agent's persistent session gets driven.
export default tool({
  description:
    "Consult a teammate agent by id and get their answer. Use this when a question is outside your expertise.",
  args: {
    target_id: tool.schema.string().describe("the teammate's id, e.g. 'reviewer'"),
    question: tool.schema.string().describe("the question to ask them"),
  },
  async execute(args, ctx) {
    const res = await fetch("http://127.0.0.1:8799/ask_agent", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        target_id: args.target_id,
        question: args.question,
        sessionID: ctx.sessionID,
        agent: ctx.agent,
      }),
    })
    return await res.text()
  },
})
