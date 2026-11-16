"""The dsh release this repo ships and tests.

The only place to bump it. ``data/agent/agents/dsh.yaml`` and the
canfar-containers Dockerfile (``ARG DSH_VERSION``) repeat it; a test keeps the
YAML in step.
"""

DSH_VERSION = "0.2.1-rc.3"
DSH_NPM = f"@deepseek-ai/dsh@{DSH_VERSION}"
DSH_INSTALL_HINT = f"npm install -g {DSH_NPM}"
