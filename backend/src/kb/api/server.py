"""``kb-api`` entry point: run the API with several worker processes."""

from __future__ import annotations

import uvicorn

from kb.config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "kb.api.app:create_app",
        factory=True,
        host=s.host,
        port=s.port,
        workers=s.workers,
        proxy_headers=True,
        timeout_graceful_shutdown=20,
        log_level=s.log_level.lower(),
    )


if __name__ == "__main__":
    main()
