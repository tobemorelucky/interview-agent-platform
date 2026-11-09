"""Send one explicitly requested ModelScope JSON probe; no DB or Agent workflow."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from interview_api.infrastructure.models.provider import (
    ModelProviderError, ModelScopeModelProvider, modelscope_connection,
)


async def probe() -> int:
    try:
        connection = modelscope_connection()
        if connection is None:
            print("Set LLM_PROVIDER=modelscope in the active environment before probing.", file=sys.stderr)
            return 2
        provider = ModelScopeModelProvider(**connection, timeout_seconds=20, max_output_tokens=128)
        # Direct provider call: no structured-output Repair, retries or fallback.
        result = await provider.generate(
            [{"role": "user", "content": 'Return only this JSON object: {"ok":true}'}], schema={},
        )
        value = json.loads(result.text)
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise ValueError("unexpected JSON probe result")
    except ModelProviderError as exc:
        # Avoid printing remote error bodies, credentials or exception chains.
        print(f"ModelScope probe failed: {exc.code} ({exc.availability_reason or 'no availability detail'})",
              file=sys.stderr)
        return 1
    except ValueError:
        print("ModelScope probe failed: response did not satisfy the JSON check.", file=sys.stderr)
        return 1
    print(json.dumps({"provider": provider.provider_name, "model": provider.model_name,
                      "json_ok": True, "usage": result.usage}))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send-once", action="store_true", required=True,
                        help="Allow one real request to ModelScope (may consume quota).")
    parser.parse_args()
    raise SystemExit(asyncio.run(probe()))
