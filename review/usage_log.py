#!/usr/bin/env python3
"""Per-call token accounting, and the metadata that makes it attributable.

The gateway is Portkey, whose analytics API reports cost per request but has no way to tell
this pipeline's traffic from the rest of the workspace -- 51,903 requests in a 48-hour window
against roughly 300 from a full corpus run. `x-portkey-metadata` is what separates them: it
travels with the request and is filterable in `/v1/analytics/*`, so a paper's cost stops
being an inference from log scraping.

Two things are recorded that the callers previously dropped:

* **`x-portkey-trace-id`**, returned on every response, which joins a local row to Portkey's
  own billing record. Getting it needs `with_raw_response`, since the parsed object carries
  the body and not the headers.
* **`cached_tokens` and `cache_write_tokens`**. These passes send 40-70k input tokens each
  and input outweighs output about 11 to 1, so a cache hit is most of what decides the bill.
  A row without them cannot be costed.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

#: One id per process, so every call a single run makes can be summed without guessing at
#: timestamps. Overridable from the environment when a driver spans several processes.
RUN_ID = os.environ.get("NS_RUN_ID") or uuid.uuid4().hex[:12]


def build_client(paper: str, stage: str):
    """An OpenAI client whose every request is tagged for the analytics API."""
    from openai import OpenAI  # noqa: PLC0415

    return OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_API_GATEWAY"),
        default_headers={
            "x-portkey-metadata": json.dumps(
                {"paper": paper, "stage": stage, "run_id": RUN_ID, "pipeline": "ns-validate"}
            )
        },
    )


def call(client, **kwargs) -> tuple[Any, dict[str, Any]]:
    """`chat.completions.create`, returning the parsed response and a usage row.

    Raw first, parsed second: the trace id is a header and is gone once the SDK has turned
    the response into a model object.
    """
    started = time.time()
    raw = client.chat.completions.with_raw_response.create(**kwargs)
    response = raw.parse()
    usage = response.usage
    out = getattr(usage, "completion_tokens_details", None)
    inp = getattr(usage, "prompt_tokens_details", None)
    row = {
        "run_id": RUN_ID,
        "trace_id": raw.headers.get("x-portkey-trace-id"),
        "model": kwargs.get("model"),
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": getattr(usage, "total_tokens", None),
        "reasoning_tokens": getattr(out, "reasoning_tokens", None) if out else None,
        "cached_tokens": getattr(inp, "cached_tokens", None) if inp else None,
        "cache_write_tokens": getattr(inp, "cache_write_tokens", None) if inp else None,
        "cache_status": raw.headers.get("x-portkey-cache-status"),
        "finish_reason": response.choices[0].finish_reason,
        "seconds": round(time.time() - started, 2),
    }
    return response, row


def record(path: Path, paper: str, stage: str, row: Mapping[str, Any],
           extra: Mapping[str, Any] | None = None) -> None:
    """Append one usage row. Never fatal: accounting must not sink an extraction."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(
                {"paper": paper, "stage": stage, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                    time.gmtime()),
                 **dict(row), **(dict(extra) if extra else {})},
                ensure_ascii=False) + "\n")
    except Exception as error:                       # noqa: BLE001
        print(f"  usage not recorded ({type(error).__name__})", flush=True)
