"""Run jobs on a CANFAR Ray cluster, or start and resize that cluster.

Shipped with ``canfar-lab``. CLI: ``canfar lab run`` / ``canfar lab cluster``.
Ray itself is provided by ray-manager / ray-worker images, not this package.
"""

from canfar_lab.version import __version__

from .executor import RayExecutor, resolve_jobs_address, run_script
from .models import (
    DataProductRef,
    ResourceRequest,
    RunSpec,
    RunStatus,
    format_memory,
    parse_memory,
)

__all__ = [
    "DataProductRef",
    "RayExecutor",
    "ResourceRequest",
    "RunSpec",
    "RunStatus",
    "format_memory",
    "parse_memory",
    "resolve_jobs_address",
    "run_script",
    "__version__",
]
