# Sourced by bin/blackhole; no host firewall changes or extra virtual machine.
GUARD_NETWORK=""
GUARD_RELAY=""

proxy_config_path() {
    printf '%s/docker-proxy/%s.proxy' "$STATE_DIR" "$1"
}

proxy_lock() {
    command -v flock >/dev/null 2>&1 || die "Docker proxy mode requires flock (util-linux in WSL2)"
    mkdir -p "${STATE_DIR}/docker-proxy"
    exec 9>"${STATE_DIR}/docker-proxy/.lock"
    flock -w 15 9 || die "another Docker proxy operation is still running"
}

proxy_unlock() {
    flock -u 9
    exec 9>&-
}

cleanup_proxy() {
    if [[ -n "$GUARD_RELAY" ]]; then
        docker_vm rm -f "$GUARD_RELAY" >/dev/null 2>&1 || true
    fi
    if [[ -n "$GUARD_NETWORK" ]]; then
        docker_vm network rm "$GUARD_NETWORK" >/dev/null 2>&1 || true
    fi
}

start_proxy_relays() {
    docker_vm start "$@" >/dev/null || return 1
    local id
    for id in "$@"; do
        # One bounded local startup check, never a background polling daemon.
        docker_vm exec "$id" node -e '
const net = require("net"), end = Date.now() + 5000;
function ready() {
    const s = net.createConnection(3128, "127.0.0.1");
    s.on("connect", () => { s.destroy(); process.exit(0); });
    s.on("error", () => { if (Date.now() >= end) process.exit(1); setTimeout(ready, 50); });
    s.setTimeout(500, () => s.destroy(new Error("timeout")));
}
ready();' >/dev/null || return 1
    done
}

# Called with the launcher's run_args array in scope (Bash dynamic scoping).
prepare_proxy() {
    local config proxy
    config="$(proxy_config_path "$PROFILE")"
    proxy="${BLACKHOLE_PROXY:-}"
    [[ -n "$proxy" || -f "$config" ]] || return 0
    [[ -n "$proxy" ]] || proxy="$(cat "$config")"
    [[ "$proxy" =~ ^http://[A-Za-z0-9._-]+:[0-9]{1,5}$ ]] \
        || die "BLACKHOLE_PROXY must be http://host:port without credentials"
    local port="${proxy##*:}"
    (( 10#$port > 0 && 10#$port <= 65535 )) || die "invalid proxy port"
    # A namespace never silently reverts to direct access in a new terminal.
    proxy_lock
    printf '%s\n' "$proxy" > "${config}.tmp.$$"
    mv "${config}.tmp.$$" "$config"
    local network_name="blackhole-${PROFILE}-$$"
    local relay_name="blackhole-proxy-${PROFILE}-$$"
    trap cleanup_proxy EXIT
    docker_vm network create --internal --label dev.blackhole.managed=1 \
        "$network_name" >/dev/null
    GUARD_NETWORK="$network_name"
    export BLACKHOLE_PROXY="$proxy"
    docker_vm create --name "$relay_name" --init --user node \
        --read-only --cap-drop ALL --security-opt no-new-privileges \
        --sysctl net.ipv4.ip_forward=0 --sysctl net.ipv6.conf.all.forwarding=0 \
        --memory 64m --cpus 0.25 --pids-limit 32 \
        --label dev.blackhole.proxy=1 --label "dev.blackhole.profile=${PROFILE}" \
        --network "$GUARD_NETWORK" --network-alias blackhole-proxy \
        --add-host host.docker.internal:host-gateway \
        -e BLACKHOLE_PROXY --entrypoint node "$DEFAULT_IMAGE" \
        /usr/local/bin/blackhole-proxy >/dev/null
    GUARD_RELAY="$relay_name"
    docker_vm network connect bridge "$GUARD_RELAY"
    if [[ ! -f "${config}.blocked" ]]; then
        start_proxy_relays "$GUARD_RELAY" \
            || die "proxy relay did not start; rebuild the Blackhole image with setup"
    fi
    proxy_unlock
    # Do not inherit the host's upstream DNS. On older engines the embedded
    # resolver can forward queries from internal networks via a host loopback
    # resolver (CVE-2024-29018). Explicit DNS keeps queries in this namespace;
    # Docker still resolves the relay's local network alias.
    run_args+=(--network "$GUARD_NETWORK" --dns 127.0.0.1 --label dev.blackhole.guard=proxy)
    local variable
    for variable in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; do
        run_args+=(-e "${variable}=http://blackhole-proxy:3128")
    done
    log "proxy-only egress (${PROFILE}); no direct internet route"
}

docker_net_command() {
    local action="${1:-status}" want="$PROFILE" all=0
    if [[ $# -gt 0 ]]; then shift; fi
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --all) all=1; shift ;;
            --profile) [[ $# -ge 2 ]] || die "--profile requires a name"; want="$2"; shift 2 ;;
            *) die "usage: blackhole net off|on|status [--all | --profile NAME]" ;;
        esac
    done
    case "$action" in off|on|status) ;; *) die "Docker proxy mode supports net off, on, or status" ;; esac
    [[ "$want" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "invalid profile name: ${want}"
    require_host_tools
    ensure_vm
    local records profile state config ids unguarded rows running
    local matched=0 failures=0
    records="$(profile_records)" || die "cannot list profiles from the selected engine"
    while read -r profile state; do
        [[ "$all" == 1 || "$want" == "$profile" ]] || continue
        matched=1
        config="$(proxy_config_path "$profile")"
        if ! rows="$(docker_vm ps --filter label=dev.blackhole.session=1 \
            --filter "label=dev.blackhole.profile=${profile}" \
            --format '{{.ID}}|{{.Label "dev.blackhole.guard"}}')"; then
            log "cannot inspect sessions for '${profile}'"
            failures=1
            continue
        fi
        unguarded="$(printf '%s\n' "$rows" | awk -F '|' 'NF && $2 != "proxy" {print $1}')"
        if [[ "$action" == status ]]; then
            running="$(docker_vm ps -q --filter label=dev.blackhole.proxy=1 \
                --filter "label=dev.blackhole.profile=${profile}")" || return 1
            state=unguarded
            if [[ -f "$config" ]]; then
                state=proxy-only
                if [[ -f "${config}.blocked" ]]; then
                    state=offline
                    [[ -z "$running" ]] || state=BLOCK-FAILED
                elif [[ -z "$running" ]]; then
                    state='proxy-only (relay stopped)'
                fi
            fi
            [[ -z "$unguarded" ]] || state=unguarded-sessions
            printf '%s\t%s\n' "$profile" "$state"
            continue
        fi
        if [[ ! -f "$config" ]]; then
            log "configure BLACKHOLE_PROXY and launch a session on '${profile}' first"
            failures=1
            continue
        fi
        proxy_lock
        if [[ -n "$unguarded" ]]; then
            log "profile '${profile}' has unguarded sessions; restart them with BLACKHOLE_PROXY first"
            failures=1
        fi
        if ! ids="$(docker_vm ps -aq --filter label=dev.blackhole.proxy=1 \
            --filter "label=dev.blackhole.profile=${profile}")"; then
            proxy_unlock
            failures=1
            continue
        fi
        if [[ "$action" == off ]]; then
            printf 'blocked\n' > "${config}.blocked.tmp.$$"
            mv "${config}.blocked.tmp.$$" "${config}.blocked"
            if [[ -n "$ids" ]]; then
                # IDs come only from Docker, never from shell input.
                if ! docker_vm stop --time 1 $ids >/dev/null; then
                    log "failed to stop proxy relays for '${profile}'; offline policy is latched, but running relays may still pass traffic"
                    failures=1
                fi
            fi
        else
            if [[ -z "$unguarded" ]] \
                && { [[ -z "$ids" ]] || start_proxy_relays $ids; }; then
                rm -f "${config}.blocked"
            else
                log "failed to enable proxy relays for '${profile}'"
                failures=1
            fi
        fi
        proxy_unlock
    done <<< "$records"
    [[ "$matched" == 1 ]] || die "unknown profile: ${want}"
    return "$failures"
}

clipboard_paste() {
    command -v powershell.exe >/dev/null 2>&1 || die "Windows clipboard requires WSL interop and powershell.exe"
    command -v wslpath >/dev/null 2>&1 || die "Windows clipboard requires wslpath"
    command -v python3 >/dev/null 2>&1 || die "Windows clipboard requires python3"
    local relative="${1:-.blackhole/clipboard/$(date +%Y%m%d-%H%M%S)-$$.png}"
    validate_project
    python3 "${ROOT_DIR}/scripts/clipboard.py" "$relative" "${ROOT_DIR}/scripts/clipboard.ps1"
}
