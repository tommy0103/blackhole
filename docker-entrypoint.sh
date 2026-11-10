#!/usr/bin/env bash
set -euo pipefail

# Colima preserves the host owner's UID in its virtiofs mount. Match that UID
# inside the container so Claude can edit the project without running as root.
workspace_uid="$(stat -c '%u' /workspace)"
workspace_gid="$(stat -c '%g' /workspace)"

# Windows bind mounts can appear root-owned. Keep the existing unprivileged
# node identity in that case instead of turning node into UID 0.
if [[ "$workspace_uid" == 0 ]]; then
    workspace_uid="$(id -u node)"
    workspace_gid="$(id -g node)"
    if ! gosu node test -w /workspace; then
        echo 'blackhole: root-owned workspace is not writable by node; rootless Docker is unsupported. Use a rootful engine and check project permissions.' >&2
        exit 1
    fi
fi

groupmod -o -g "$workspace_gid" node
usermod -o -u "$workspace_uid" -g "$workspace_gid" node

# Keep Blackhole visible for the whole Claude session, not just during launch,
# and register a SessionStart hook that records profile <-> session <-> path.
mkdir -p /home/node/.claude/blackhole/projects /home/node/.claude/blackhole/sessions
node <<'NODE'
const fs = require('fs');
const path = '/home/node/.claude/settings.json';
let settings = {};
if (fs.existsSync(path)) {
    settings = JSON.parse(fs.readFileSync(path, 'utf8'));
}
settings.statusLine = {
    type: 'command',
    command: '/usr/local/bin/blackhole-statusline'
};

const hook = '/usr/local/bin/blackhole-session-hook';
settings.hooks = settings.hooks || {};
for (const event of ['SessionStart', 'SessionEnd']) {
    settings.hooks[event] = Array.isArray(settings.hooks[event])
        ? settings.hooks[event]
        : [];
    const installed = settings.hooks[event].some((group) =>
        Array.isArray(group && group.hooks) &&
        group.hooks.some((entry) => entry && entry.command === hook)
    );
    if (!installed) {
        settings.hooks[event].push({
            matcher: '',
            hooks: [{ type: 'command', command: hook }]
        });
    }
}
fs.writeFileSync(path, JSON.stringify(settings, null, 2) + '\n');

// Record the profile <-> host path mapping as soon as a container starts, so
// projects appear in the registry even before Claude creates a session file.
const project = process.env.BLACKHOLE_PROJECT_PATH;
if (project) {
    const crypto = require('crypto');
    const id = crypto.createHash('sha256').update(project).digest('hex').slice(0, 16);
    fs.writeFileSync(
        `/home/node/.claude/blackhole/projects/${id}.json`,
        JSON.stringify({
            path: project,
            profile: process.env.BLACKHOLE_PROFILE || '',
            lastUsed: new Date().toISOString()
        }, null, 2) + '\n'
    );
}
NODE

chown -R "$workspace_uid:$workspace_gid" /home/node

if [[ $# -eq 0 ]]; then
    set -- claude
fi

exec gosu node "$@"
