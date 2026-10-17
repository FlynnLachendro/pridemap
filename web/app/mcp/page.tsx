import { API } from "@/lib/api";
import { AgentConsole } from "@/components/agent-console";
import { Collapsible } from "@/components/expandable";

type Tool = { name: string; description: string; input_schema: { properties: Record<string, { type?: string; description?: string; enum?: string[] }>; required?: string[] } };
type Out = { server: string; transport: string[]; tools: Tool[]; resources: { uri: string; description: string }[] };

const REPO = "/Users/flynnlachendro/Coding/Personal/pridemap";

export default async function McpPage() {
  let d: Out | null = null;
  try { d = await (await fetch(`${API}/mcp/tools`, { cache: "no-store" })).json(); } catch { d = null; }
  return (
    <div className="grid gap-8 max-w-5xl">
      <div className="grid gap-3">
        <h1 className="text-4xl">PRIDE Map for AI agents</h1>
        <div className="grid sm:grid-cols-3 gap-3">
          <div className="card p-4 grid gap-1"><div className="label">What MCP is</div><p className="text-sm">A standard plug. An AI assistant (Claude Desktop, Claude Code, Cursor, your own agent) connects to a server that offers <b>tools</b>, and can then call them itself while it reasons.</p></div>
          <div className="card p-4 grid gap-1"><div className="label">What this server offers</div><p className="text-sm">Everything the app does: search by method tags, turn a question into filters, read a dataset with evidence, compare datasets, build a set‑up sheet, look up a protein.</p></div>
          <div className="card p-4 grid gap-1"><div className="label">Why it matters</div><p className="text-sm">Your assistant can find and vet a dataset for you mid‑conversation, with the same evidence a person sees, instead of you clicking through PRIDE.</p></div>
        </div>
      </div>

      <section className="grid gap-3">
        <h2 className="text-2xl">Watch an agent use it</h2>
        <AgentConsole />
      </section>

      <section className="grid gap-3">
        <h2 className="text-2xl">The tools</h2>
        {!d && <p className="text-warn text-sm">API not reachable.</p>}
        {d && (
          <div className="grid gap-2">
            {d.tools.map((t) => (
              <Collapsible key={t.name} title={t.name} summary={t.description.slice(0, 110) + (t.description.length > 110 ? "…" : "")}>
                <div className="grid gap-2 text-sm">
                  <p>{t.description}</p>
                  <table className="w-full text-xs">
                    <thead><tr className="border-b"><th className="label text-left p-1.5">parameter</th><th className="label text-left p-1.5">type</th><th className="label text-left p-1.5">values / meaning</th></tr></thead>
                    <tbody>
                      {Object.entries(t.input_schema.properties).map(([k, p]) => (
                        <tr key={k} className="border-b last:border-0"><td className="p-1.5 font-mono">{k}{t.input_schema.required?.includes(k) && <span className="text-warn"> *</span>}</td><td className="p-1.5 font-mono text-muted-foreground">{p.type}</td><td className="p-1.5 text-muted-foreground">{p.enum ? p.enum.join(" · ") : p.description ?? ""}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Collapsible>
            ))}
            {d.resources.map((r) => (
              <div key={r.uri} className="card p-3 text-sm flex gap-3 items-baseline"><span className="pill">resource</span><span className="font-mono">{r.uri}</span><span className="text-muted-foreground">{r.description}</span></div>
            ))}
          </div>
        )}
      </section>

      <section className="grid gap-3">
        <h2 className="text-2xl">Connect your assistant</h2>
        <div className="grid md:grid-cols-3 gap-3">
          <div className="card p-4 grid gap-2">
            <div className="font-bold">Claude Code</div>
            <pre className="text-[11px] font-mono bg-surface-2 rounded-md p-2 overflow-x-auto">{`claude mcp add pridemap -- \\
  uv run ${REPO}/api/mcp_server.py`}</pre>
            <p className="text-xs text-muted-foreground">Then ask Claude Code: “find me a mouse liver phospho DIA dataset and tell me if it’s comparable with PXD055942”.</p>
          </div>
          <div className="card p-4 grid gap-2">
            <div className="font-bold">Claude Desktop</div>
            <pre className="text-[11px] font-mono bg-surface-2 rounded-md p-2 overflow-x-auto">{`{
  "mcpServers": {
    "pridemap": {
      "command": "uv",
      "args": ["run", "${REPO}/api/mcp_server.py"]
    }
  }
}`}</pre>
            <p className="text-xs text-muted-foreground">Settings → Developer → Edit config. The tools appear in the chat.</p>
          </div>
          <div className="card p-4 grid gap-2">
            <div className="font-bold">Over HTTP</div>
            <pre className="text-[11px] font-mono bg-surface-2 rounded-md p-2 overflow-x-auto">{`uv run api/mcp_server.py --http
# streamable HTTP MCP endpoint:
http://localhost:8010/mcp`}</pre>
            <p className="text-xs text-muted-foreground">For hosted agents and anything that speaks MCP over HTTP. Plain REST stays at <a className="underline" href={`${API}/docs`}>{API}/docs</a>.</p>
          </div>
        </div>
      </section>
    </div>
  );
}
