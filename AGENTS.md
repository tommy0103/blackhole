# AGENTS.md

This file provides instructions for AI coding agents operating in two roles:

1. **Operator Mode (User Usage Scenarios)** — when a user asks you to install, configure, bind profiles, add project tools, manage sessions, or control networking using Blackhole.
2. **Developer Mode (Repository Maintenance)** — when a user asks you to modify, debug, or extend Blackhole itself.

---

## Part 1: Operator Playbook (Using Blackhole for the User)

### 1. Inspect Current State First

Before making changes, run read-only status checks (these never disturb running Claude sessions):

```bash
./bin/blackhole status          # Current profile, symlink protection, image status, active containers
./bin/blackhole profiles        # All Colima VMs, memory, and recorded project/session counts
./bin/blackhole bindings        # Host directory -> Colima profile bindings
./bin/blackhole net status      # Outbound network state (online/offline/stopped) across VMs
./bin/blackhole net-watch status
./bin/blackhole clipboard status
```

### 2. First-Time Setup & Symlink Installation

- **Never install `@anthropic-ai/claude-code` on the macOS host.** The real `claude` CLI lives only inside the Colima VM Docker image (`blackhole-claude:latest`).
- Ensure Colima is running with Apple Virtualization (`vz`) and `virtiofs`:
  ```bash
  colima start --vm-type vz --mount-type virtiofs --cpu 4 --memory 8 --disk 60
  ```
- Build the base image and install symlinks (`~/.local/bin/claude` and `~/.local/bin/blackhole`):
  ```bash
  ./bin/blackhole setup
  ./bin/blackhole install
  ```
  If the user already has a host `claude` binary at the target prefix and wants Blackhole to take over, pass `--force`.

### 3. Creating & Binding a Dedicated Colima Profile for a Project

On Linux/WSL2, use the existing Docker Engine instead: `docker info` must work
for the normal user. `BLACKHOLE_BACKEND=auto` selects Docker on Linux and Colima
on macOS. Do not install Colima or add a nested VM on WSL2. Docker Desktop WSL2
integration is optional; do not install it over a working native engine.
`BLACKHOLE_PROFILE` overrides the legacy `BLACKHOLE_COLIMA_PROFILE` and bindings.
Docker profiles share one engine/image cache and isolate credential volumes;
binding a Docker profile does not allocate a new VM.
Sessions require a rootful engine accessible to the normal user. Rootless
Docker maps host-owned files to container UID 0 and is explicitly rejected
before session setup; do not work around this with `sudo`, UID 0 sessions,
recursive ownership changes, or world-writable project permissions.

When the user wants a separate VM (custom CPU/RAM or isolated Docker/credentials volume) for a specific project directory:

```bash
# 1. Create and start the Colima VM profile with virtiofs
colima start --profile <profile> --vm-type vz --mount-type virtiofs --cpu 4 --memory 16 --disk 60

# 2. Build the Blackhole base image inside that profile
BLACKHOLE_COLIMA_PROFILE=<profile> ./bin/blackhole setup

# 3. Bind the user's project directory to the profile (stored in ~/.blackhole/bindings/)
./bin/blackhole bind <profile> /path/to/project
```

Subdirectories inherit the binding automatically. Precedence is: `BLACKHOLE_PROFILE` > `BLACKHOLE_COLIMA_PROFILE` > nearest ancestor directory binding > `default`.

### 4. Adding Per-Project Linux Tools (`.blackhole/Dockerfile`)

macOS binaries (`/opt/homebrew/bin`) are Mach-O and cannot be mounted into the Linux container. When a user needs extra tools (`gh`, `jq`, `ffmpeg`, `cargo`, C/C++ headers, etc.) inside a project's Blackhole container:

1. Create `<project>/.blackhole/Dockerfile`:
   ```dockerfile
   ARG BLACKHOLE_BASE=blackhole-claude:latest
   FROM ${BLACKHOLE_BASE}

   RUN apt-get update \
       && apt-get install -y --no-install-recommends <packages> \
       && rm -rf /var/lib/apt/lists/*
   ```
2. Run `(cd <project> && blackhole setup)` to build `blackhole-claude:project-<hash>`.
3. **Never place secrets or SSH keys inside `<project>/.blackhole/`**, and remember that only `<project>/.blackhole/` is sent as the Docker build context.

### 5. Managing, Exporting & Migrating Sessions

- **Find sessions across all running VMs**:
  ```bash
  blackhole sessions
  ```
- **Preview or resume by session ID prefix** (from any directory):
  ```bash
  blackhole resume --dry-run <prefix>
  blackhole resume [--profile <name>] <prefix>
  ```
- **Export a session transcript to the host** (`~/.blackhole/sessions/<profile>/<session-id>.jsonl`):
  ```bash
  blackhole export [--profile <name>] [--output <file>] <prefix>
  blackhole export-watch start [--profile <name>] [--interval 60] <prefix>
  ```
- **Migrate a session between profiles**:
  - Always stage archives under `$HOME` (e.g., `$HOME/.cache/blackhole-migrate`), **never `/tmp`**, because Colima only mounts `$HOME` by default.
  - Copy both `.claude/.credentials.json` and `.claude/projects/-workspace/<session-id>.jsonl` from `blackhole-claude-home-<src>` to `blackhole-claude-home-<dst>`, then remove the staging directory immediately.

### 6. Network Control & Fail-Close Proxy Protection (`net` / `net-watch`)

The commands below describe Colima. For Docker/WSL2, launch with
`BLACKHOLE_PROXY=http://host:port` (an HTTP proxy reachable from containers).
The endpoint is saved per profile. Each session joins an internal network and
uses an unprivileged TCP relay capped at 64 MiB/0.25 CPU with no host ports,
project mounts, credentials, or Linux capabilities. There is no direct external
route when the upstream is down, and no polling daemon. `net off/on/status`
controls only these relays; `net off` also latches new sessions offline.
Guarded sessions set `--dns 127.0.0.1` to avoid inherited host DNS forwarding
on older Docker engines; retain local relay alias resolution and never remove
this override without verifying DNS confinement as well as TCP confinement.
Existing unguarded sessions must be restarted before this protection applies.
Do not apply Colima iptables scripts to a shared WSL host. VM `net ipv6` and
`net-watch` remain Colima-only. The upstream proxy's routing determines exit IP.

On WSL2, use `blackhole clipboard paste [relative.png]` after Win+Shift+S.
It reads the Windows clipboard once via `scripts/clipboard.ps1`, preserves it,
and returns PNG data without passing a project output path to Windows.
`scripts/clipboard.py` requires host Python 3 and publishes the complete PNG
inside the project via pinned directory descriptors and a no-clobber hard link,
printing its `/workspace` path. Never replace this with a checked-path `mv`:
the running agent can replace paths between validation and publication.
Do not start the macOS clipboard mirror on WSL or inject keys into Claude's TUI.

- **Block or restore outbound VM internet**:
  ```bash
  blackhole net off --all          # or --profile <name>
  blackhole net on --all
  blackhole net ipv6 off --all     # Persist sysctl + ip6tables IPv6 block
  ```
- **Fail-close proxy watcher (`net-watch`)**:
  - Use `blackhole net-watch start` when the host routes traffic through a TUN proxy (Shadowrocket, Surge, Clash) and must never leak direct connections if the proxy exits.
  - **Important**: `net-watch` is **fail-close**. Both `net-watch start` (before probes succeed) and `net-watch stop` leave VM networking **blocked**. To restore normal networking after stopping `net-watch`, you must explicitly run `blackhole net on --all`.
  - For non-TUN HTTP proxies, pass `--no-require-tun --proxy <proxy-url> --url <probe-url>`.

### 7. Cross-Agent File Signaling (`blackhole-signal`)

To coordinate between a host agent/runner (e.g., Codex or a test harness) and Claude running inside Blackhole:

- **Inside the Blackhole container**:
  ```bash
  blackhole-signal wait --file .blackhole/signals/task.json --timeout 1800
  ```
- **On the macOS host**:
  ```bash
  blackhole signal send --file /path/to/project/.blackhole/signals/task.json \
    --data '{"status":"done","summary":"All checks passed"}'
  ```

---

## Part 2: Developer Playbook (Modifying Blackhole)

### 1. Architecture & File Map

| File | Runs Where | Responsibility |
| --- | --- | --- |
| [`bin/blackhole`](bin/blackhole) | macOS / Linux / WSL2 (Bash) | CLI entrypoint, backend/profile resolution, container launcher, Colima daemons |
| [`lib/docker-backend.sh`](lib/docker-backend.sh) | Linux / WSL2 (Bash) | Docker proxy-only networks, relay lifecycle, profile network controls, Windows clipboard bridge |
| [`blackhole-proxy.cjs`](blackhole-proxy.cjs) | Unprivileged relay container | TCP forwarding to one explicit upstream HTTP proxy, no direct fallback |
| [`scripts/clipboard.ps1`](scripts/clipboard.ps1) | Windows (STA PowerShell) | Return clipboard PNG data as base64, without opening project paths |
| [`scripts/clipboard.py`](scripts/clipboard.py) | WSL2 (Python 3) | Capture clipboard data and atomically publish it within pinned project directories |
| [`Dockerfile`](Dockerfile) | Colima VM (Build) | Debian `node:22-bookworm-slim` base image, system packages, `@anthropic-ai/claude-code`, helper scripts |
| [`docker-entrypoint.sh`](docker-entrypoint.sh) | Container (Root -> `node`) | Matches `node` UID/GID to `/workspace`, installs statusLine + `SessionStart`/`SessionEnd` hooks into `/home/node/.claude/settings.json`, records project registry entry, drops privileges via `gosu node` |
| [`blackhole-session-hook.sh`](blackhole-session-hook.sh) | Container (`node`) | Claude hook (`SessionStart`/`SessionEnd`) updating `/home/node/.claude/blackhole/sessions/<id>.json` and `projects/<hash>.json` |
| [`blackhole-registry.mjs`](blackhole-registry.mjs) | Container (`node`) | Installed as `/usr/local/bin/blackhole-registry`; queries session/project records inside the config volume and outputs TSV |
| [`blackhole-signal.mjs`](blackhole-signal.mjs) | Container (`node`) | Installed as `/usr/local/bin/blackhole-signal`; implements `read` and `wait` with content-stability polling |
| [`blackhole-statusline.sh`](blackhole-statusline.sh) | Container (`node`) | Renders the magenta `⬤ BLACKHOLE` badge in Claude Code's status bar |
| [`blackhole-bashrc.sh`](blackhole-bashrc.sh) | Container (`node`) | Prompt configuration for `blackhole shell` |

### 2. Core Invariants (Never Violate These)

1. **Host Fingerprint & Filesystem Isolation**:
   - Never mount host `$HOME`, `~/.ssh`, `~/.gitconfig`, Docker sockets, or broad directories (`/`, `/Users`, `$HOME`) by default in `run_container`.
   - Keep `--hostname blackhole` and `--security-opt no-new-privileges` (unless FUSE is explicitly enabled for the project).
   - Keep the environment variable allowlist in `run_container` minimal (`TERM`, `COLORTERM`, `NO_COLOR`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`). Never pass through `env` wholesale, as host locale/region/hardware variables can leak host identity.
2. **Non-Disruptive Read-Only Probes**:
   - Helper containers spawned by `status`, `scan_registry`, `show_profiles`, `export_session`, or `run_export_watch` must always run with `--network none` and mount the config volume `:ro` (or not at all in `status`) so they never race with an active Claude session.
3. **Atomic Writes Everywhere**:
   - Host bindings, watcher state files, exported JSONL files, registry JSON files, and signal files must always be written to a temporary file in the same directory (e.g., `${file}.tmp.$$`) and renamed (`mv`) into place.
   - Clipboard PNGs use a same-directory temporary file and an atomic no-clobber hard link instead of rename, so an existing target cannot be overwritten or followed as a directory. All project operations use directory descriptors and refuse symlink traversal; writes use the already-open temporary file descriptor.
4. **Fail-Close Network Watchdog**:
   - Any changes to `run_net_watch`, `run_net_guard`, or `net_watch_stop` must preserve fail-close behavior: block network before the first probe, block network if the watcher exits or heartbeat ages out, and keep network blocked on `net-watch stop`.
5. **Host Launcher vs Container Image Boundary**:
   - Edits to [`bin/blackhole`](bin/blackhole) apply immediately to the next command invocation.
   - Edits to [`Dockerfile`](Dockerfile) or any script copied into the image (`docker-entrypoint.sh`, `blackhole-*.sh`, `blackhole-*.mjs`) require running `./bin/blackhole setup` on each active Colima profile to take effect for new containers.
   - Note that [`blackhole-registry.mjs`](blackhole-registry.mjs) and [`blackhole-signal.mjs`](blackhole-signal.mjs) are copied into `/usr/local/bin/` **without file extensions** inside the container (`Dockerfile` lines 23–24), where Node executes extensionless scripts as CommonJS (`require(...)`). Keep CommonJS compatibility in those scripts unless `Dockerfile` is updated in tandem.

### 3. Documentation Parity Checklist

Whenever you add or modify a subcommand, flag, or environment variable in [`bin/blackhole`](bin/blackhole):

1. Update `usage()` in [`bin/blackhole`](bin/blackhole).
2. Update both [`README.md`](README.md) (English) and [`README.zh-CN.md`](README.zh-CN.md) (Chinese), maintaining 1:1 structural parity (identical heading count, table count, code block count, and valid relative links).
3. Update this [`AGENTS.md`](AGENTS.md) if the operational workflow or architecture changed.

### 4. Verification

Run `python3 -m unittest discover -s tests -v`. Launcher tests use fake engine
commands and never start a VM/container or contact an API. Relay tests use Node
and local sockets only (skipped if host Node is absent). CI runs on Linux and
macOS, including the system Bash. For real Docker smoke tests, use a unique
profile/state directory, bound runtime resources, a controlled local proxy, and
remove only test-created containers, volumes, and networks afterwards. Check
direct-route failure, proxy loss, net off/on, non-root execution, and persistence;
do not authenticate Claude or alter the user's clipboard to run a test.
Run `python3 tests/smoke_permissions.py IMAGE` against an existing local image
to verify the local entrypoint with ordinary `0755`/`0644` project permissions,
root-owned writable Windows-style mounts, and root-owned unwritable mounts.
This uses a disposable network-disabled container with no host mounts or volumes.
