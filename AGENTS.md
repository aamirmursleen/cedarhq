# Fleet VM115 Build Policy

- This machine is a runtime, control, database, or application host. It is not a build worker.
- Never run package compilation, bundling, Docker/Buildx, BuildKit, Nixpacks, or other software builds locally, including by absolute binary path.
- Run `vm115-build` from the Git worktree for manual validation. Common `npm`, `pnpm`, `yarn`, `bun`, Docker, Nixpacks, and framework build commands are routed there automatically.
- GitHub -> Dokploy deployments build on VM115 `dokploy-build`; destination VMs only pull and run completed images.
- Do not disable or bypass `vm115-build-enforcement.service`, the command router, the restricted VM115 key, or the shared build-slot gate.
- Never run full Playwright/browser suites on a production VM. Use VM115 or another dedicated non-production runner.
- If VM115 routing is unavailable, report the failure and stop; do not fall back to a local build.

