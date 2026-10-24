"""Route ``canfar lab`` without loading the platform client.

The platform ``canfar`` console script reads ``~/.canfar/config.yaml`` while
importing. A file written by a newer client aborts every command, including
``canfar lab studio``, which does not use that file.
"""

from __future__ import annotations

import sys


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["lab"]:
        sys.argv = [sys.argv[0], *args[1:]]
        from canfar_lab.cli.main import main_entry

        main_entry()
        return
    from canfar.cli.main import main as platform_main

    raise SystemExit(platform_main())


if __name__ == "__main__":
    main()
