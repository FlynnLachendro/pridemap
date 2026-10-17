# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2"]
# ///
"""PRIDE Map as an MCP server: the same search, compare, dataset, set-up sheet and protein tools the web app uses,
available to any MCP client (Claude Desktop, Claude Code, Cursor, your own agent).

  uv run api/mcp_server.py              # stdio (what Claude Desktop / Claude Code spawn)
  uv run api/mcp_server.py --http       # streamable HTTP on http://localhost:8010/mcp

Needs the PRIDE Map API running (./dev.sh or `uv run api/main.py`), default http://localhost:8000; override with PRIDEMAP_API.

Claude Desktop (claude_desktop_config.json):
  {"mcpServers": {"pridemap": {"command": "uv", "args": ["run", "/ABSOLUTE/PATH/pridemap/api/mcp_server.py"]}}}
Claude Code:
  claude mcp add pridemap -- uv run /ABSOLUTE/PATH/pridemap/api/mcp_server.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from mcp.server.mcpserver import MCPServer  # noqa: E402

import tools  # noqa: E402

mcp = MCPServer(
    "PRIDE Map",
    instructions=(
        "PRIDE Map finds mass-spectrometry proteomics datasets in PRIDE by how they were made (method tags read from protocols and papers, "
        "each with confidence and evidence). Typical flow: ask_question or search_datasets → get_dataset / why_candidate on the best ones → "
        "compare_datasets before pooling → setup_sheet to reanalyse. protein_info tells you whether a protein is detectable by tryptic LC-MS. "
        "Protein names are not in dataset text, so filter on method and tissue, not on the protein."
    ),
)


def _register(t):
    schema = t["input_schema"]
    props = schema.get("properties", {})
    required = set(schema.get("required", []))

    # Build a function with an explicit signature so the SDK derives the tool's input schema from it.
    import inspect

    params = []
    ann = {}
    for name, p in props.items():
        typ = {"string": str, "integer": int, "array": list, "boolean": bool}.get(p.get("type"), str)
        default = inspect.Parameter.empty if name in required else p.get("default", None)
        params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=typ if name in required else (typ | None)))
        ann[name] = typ if name in required else (typ | None)

    def impl(**kwargs):
        return json.dumps(t["fn"](**{k: v for k, v in kwargs.items() if v is not None}), ensure_ascii=False)

    impl.__name__ = t["name"]
    impl.__doc__ = t["description"]
    impl.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    impl.__annotations__ = {**ann, "return": str}
    mcp.tool(name=t["name"], description=t["description"])(impl)


for _t in tools.TOOLS:
    _register(_t)


@mcp.resource("pridemap://vocab")
def vocab() -> str:
    """The closed vocabularies behind the method tags (what each filter can be)."""
    return json.dumps(tools.TAG_ENUMS, indent=1)


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8010, json_response=True, stateless_http=True)
    else:
        mcp.run(transport="stdio")
