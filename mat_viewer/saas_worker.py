"""Redis-backed MatterVis SaaS worker entrypoint.

The web process and this worker use the same repository, object-store and job
contracts.  Local development defaults to the in-process runner instead.
"""

from __future__ import annotations

import os

from .saas import SaaSConfig, SaaSService


def main() -> None:
    config = SaaSConfig.from_env(os.environ.get("MATTERVIS_ROOT"))
    service = SaaSService(config=config)
    try:
        service.run_redis_worker()
    finally:
        service.close()


if __name__ == "__main__":
    main()
