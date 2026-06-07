"""
Tools layer — MCP server exposing data tools + rule tools to LLM.

Usage:
  # Get OpenAI-format tool schemas for LLM:
  from tools.server import get_openai_tool_schemas
  tools_list = get_openai_tool_schemas()

  # Execute a tool call (by name):
  from tools.server import execute_tool
  result = await execute_tool("search_activities", {"scenario": "family"})
"""
