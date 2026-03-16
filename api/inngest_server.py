import os

from fastapi import FastAPI
from inngest import fast_api

from workflows.cod_flow import inngest_client, wait_and_cancel


def wire_inngest(app: FastAPI) -> None:
    """Expose Inngest function handlers at /api/inngest."""
    signing_key = str(os.getenv("INNGEST_SIGNING_KEY") or "").strip()
    if not signing_key:
        print("Inngest wiring skipped: INNGEST_SIGNING_KEY is missing")
        return
    fast_api.serve(
        app=app,
        client=inngest_client,
        functions=[wait_and_cancel],
    )
