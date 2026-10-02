"""Generate TypeScript types from FastAPI OpenAPI spec.

Run: uv run generate-types

This script reads the FastAPI OpenAPI specification and generates
TypeScript types for the frontend codegen.

M0: generates a basic skeleton. Full generation deferred to M1.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Run type generation from FastAPI OpenAPI spec."""
    # Get the OpenAPI spec from the running app
    openapi_url = "http://127.0.0.1:8000/openapi.json"

    # Try to fetch the spec
    try:
        import httpx
        resp = httpx.get(openapi_url, timeout=5)
        resp.raise_for_status()
        spec = resp.json()
    except Exception as e:
        print(f"Warning: could not fetch OpenAPI spec: {e}")
        print("Generating basic skeleton types...")
        spec = {"paths": {}, "components": {"schemas": {}}}

    # Generate basic TypeScript types
    types_dir = Path("frontend/src/api/generated")
    types_dir.mkdir(parents=True, exist_ok=True)

    # Write a basic types file
    types_file = types_dir / "generated-types.ts"
    with open(types_file, "w") as f:
        f.write("// Auto-generated types from FastAPI OpenAPI spec\n")
        f.write("// Run: uv run generate-types\n")
        f.write("// This is a skeleton — full types generated per module\n")
        f.write("export interface LifeOSAppConfig {\n")
        f.write("  apiUrl: string;\n")
        f.write("  wsUrl: string;\n")
        f.write("}\n")

    print(f"Generated types skeleton at {types_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())