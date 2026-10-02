"""Redis-backed MatterVis SaaS worker entrypoint."""

from __future__ import annotations

import os

from .config import SaaSConfig
from .service import SaaSService


def main() -> None:
    config = SaaSConfig.from_env(os.environ.get("MATTERVIS_ROOT"))
    service = SaaSService(config=config)
    try:
        service.run_redis_worker()
    finally:
        service.close()


if __name__ == "__main__":
    main()
