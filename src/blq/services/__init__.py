"""Shared service layer for blq.

Services contain pure business logic called by both CLI and MCP.
Each function takes a BlqStorage instance and returns structured data.
No argparse, no MCP, no output formatting.
"""
