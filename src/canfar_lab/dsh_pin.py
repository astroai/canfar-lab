"""dsh is not pinned: Studio uses the newest release.

"Newest" is the highest semver across npm dist-tags (see ``dsh_release``);
npm's ``latest`` tag can lag ``next``. The Studio image resolves it at every
build (canfar-containers ``scripts/dsh-newest.sh``).

``DSH_VERSION`` is only the offline fallback: the newest release Studio was
last tested with, used when the registry cannot be reached.
"""

DSH_VERSION = "0.2.2-rc.3"
DSH_INSTALL_HINT = "canfar lab studio --install-dsh  (installs the newest dsh)"
