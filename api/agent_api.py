"""The in-app agent console: Claude using PRIDE Map's tools (the same ones the MCP server exposes), streamed as
newline-delimited JSON events so the page can show each tool call as it happens. Also serves the tool list for /mcp."""
import json
import os
import pathlib
import sys

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import tools  # noqa: E402

router = APIRouter()

AGENT_SYSTEM = """You are a proteomics data-reuse assistant with tools over PRIDE Map. Work like a careful bioinformatician:
1. Understand the question. If it names a protein, call protein_info first to judge detectability and tissue.
2. Find candidates with ask_question or search_datasets (method filters, not protein names).
3. Look closer at the best 1–3 with get_dataset or why_candidate; use compare_datasets if the user wants to pool datasets.
4. If they plan to reanalyse, call setup_sheet.
Then answer in plain English, under 180 words: the recommended dataset(s) with accession, why they fit, the main caveat, and the next step.
Cite accessions like PXD029247. Never invent datasets or methods; everything comes from the tools."""


class AgentIn(BaseModel):
    q: str
    max_steps: int = 8


@router.get("/mcp/tools")
def mcp_tools():
    return {"server": "PRIDE Map", "transport": ["stdio", "streamable-http http://localhost:8010/mcp"],
            "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools.TOOLS],
            "resources": [{"uri": "pridemap://vocab", "description": "closed vocabularies behind the method tags"}]}


@router.post("/agent")
def agent(body: AgentIn):
    def gen():
        def ev(kind, **data):
            return json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n"
        if not os.environ.get("ANTHROPIC_API_KEY"):
            yield ev("error", message="No ANTHROPIC_API_KEY in the API's environment; the agent console needs Claude.")
            return
        import anthropic
        client = anthropic.Anthropic(timeout=120, max_retries=1)
        model = os.environ.get("AGENT_MODEL", "claude-sonnet-5")
        messages = [{"role": "user", "content": body.q}]
        yield ev("start", model=model, tools=[t["name"] for t in tools.TOOLS])
        for step in range(body.max_steps):
            try:
                resp = client.messages.create(model=model, max_tokens=1500, system=AGENT_SYSTEM, tools=tools.claude_tools(), messages=messages,
                                              output_config={"effort": "low"})
            except Exception as e:
                yield ev("error", message=str(e)[:300])
                return
            messages.append({"role": "assistant", "content": resp.content})
            text = "".join(b.text for b in resp.content if b.type == "text")
            if text.strip():
                yield ev("text", text=text, final=resp.stop_reason != "tool_use")
            if resp.stop_reason != "tool_use":
                yield ev("done", steps=step + 1, usage={"in": resp.usage.input_tokens, "out": resp.usage.output_tokens})
                return
            results = []
            for b in resp.content:
                if b.type != "tool_use":
                    continue
                args = b.input if isinstance(b.input, dict) else json.loads(b.input)
                yield ev("tool_call", id=b.id, name=b.name, args=args)
                try:
                    out = tools.call(b.name, args)
                    preview = out
                    yield ev("tool_result", id=b.id, name=b.name, ok=True, result=preview)
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, ensure_ascii=False)[:12000]})
                except Exception as e:
                    yield ev("tool_result", id=b.id, name=b.name, ok=False, error=str(e)[:300])
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"error: {e}", "is_error": True})
            messages.append({"role": "user", "content": results})
        yield ev("done", steps=body.max_steps, truncated=True)

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
