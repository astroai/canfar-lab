# AstroAI Studio

Laptop CLI: `astroai studio` (this package).

CANFAR contributed image and Skaha proxy details live in
[canfar-containers docs/STUDIO.md](https://github.com/astroai/canfar-containers/blob/main/docs/STUDIO.md)
(`images.canfar.net/astroai/studio`).

```bash
astroai studio --prepare     # review-bench + dotenv + profile + bash timeout patch
astroai studio               # dsh web on :3080
astroai studio --skills      # agentskills.io onboarding
```

On Skaha, launch the **studio** contributed session — do not put `dsh web`
behind vscode/notebook `/proxy/PORT/`.
