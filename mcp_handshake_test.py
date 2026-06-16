"""Full MCP stdio handshake: initialize -> list_tools -> call carsim_info.

Proves the server works over the same transport Claude Code uses.
Run:  .venv\\Scripts\\python.exe mcp_handshake_test.py
"""

import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command="python", args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("TOOLS:", names)
            # 10 core + 7 database/dictionary + 6 table/link/assembly tools.
            expected = {
                "carsim_info", "launch_gui", "list_examples", "read_parsfile",
                "write_parsfile", "run_solver", "generate_simfile",
                "scaffold_cosim", "read_results", "run_cosim_headless",
                "list_libraries", "browse_library", "find_dataset",
                "get_dataset", "set_dataset", "describe_keyword",
                "build_keyword_dictionary",
                "set_table", "get_links", "set_link", "resolve_assembly",
                "clone_dataset", "consolidate_run",
            }
            missing = expected - set(names)
            assert not missing, f"missing tools: {missing}"
            assert len(names) == len(expected), f"tool count {len(names)} != {len(expected)}"

            result = await session.call_tool("carsim_info", {})
            payload = json.loads(result.content[0].text)
            print("carsim_info.ok =", payload.get("ok"))
            print("vs_sf exists  =", payload["vs_sf_mex"]["exists"])
            print("matlab exists =", payload["matlab_exe"]["exists"])
            print("carsim_db     =", payload["carsim_db"])
            assert payload["ok"] is True
            print("\nMCP HANDSHAKE OK")


if __name__ == "__main__":
    asyncio.run(main())
