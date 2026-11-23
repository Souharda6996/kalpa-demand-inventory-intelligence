"""Write the OpenAPI schema to a file (used by `pnpm gen:api`)."""

import json
import sys
from pathlib import Path

from kalpa_api.main import create_app


def main() -> None:
    """Export openapi.json to the path given as argv[1]."""
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(create_app().openapi(), indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
