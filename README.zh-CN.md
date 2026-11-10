# Blackhole

[English](README.md) · 中文

通过 macOS 的 [Colima](https://github.com/abiosoft/colima) 或 Linux/Windows WSL2 已有的 Docker Engine，在 Linux 容器内运行 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)。只挂载当前项目目录，登录卷在会话结束后保留。WSL2 还支持 Windows 剪贴板截图和仅通过指定代理出网。

## 背景

直接在 macOS 宿主机上运行 `claude` 会同时暴露两件事：一是宿主机文件系统（`~/.ssh`、云厂商凭据、Docker socket 及其他代码仓库），二是**宿主机环境指纹**（macOS 硬件标识、系统地区与语言设置、宿主机用户名、环境变量，以及代理闪退时的真实网络出口）。

Blackhole 用一个纯 Bash 启动器脚本（`~/.local/bin/claude -> bin/blackhole`）接管宿主机上的 `claude` 命令。**宿主机不需要安装 Claude Code** —— 真正的 `@anthropic-ai/claude-code` 安装在 Docker 镜像内部。在项目中输入 `claude` 时，macOS 使用 Colima、Linux/WSL2 使用已有 Docker Engine，启动一次性 Linux 容器。下述 Colima 特性对应的 Docker 实现见安装与网络控制章节：

- **仅挂载当前目录**：通过 Colima 的 `virtiofs` 只把 `$PWD` 挂载到容器内 `/workspace`，绝不挂载宿主机 `$HOME`、SSH Agent 或 Docker socket。
- **宿主机指纹隔离**：运行在干净的 Debian 容器内，固定 `--hostname blackhole`，自动将 `node` 用户 UID/GID 对齐到 `/workspace` 属主，并严格限制透传的环境变量白名单（仅 `TERM`、`COLORTERM`、`NO_COLOR` 及可选 API Key），屏蔽宿主机系统特征、用户名与环境变量。
- **防泄漏网络出口保护（Fail-Close）**：内置 IPv6 彻底禁用（`net ipv6 off`）、VM 一键断网（`net off`）以及带独立心跳守护进程的代理监控（`net-watch`）；一旦宿主机 TUN 代理掉线立即掐断所有 VM 网络，防止流量回退直连暴露真实 IP。
- **登录态与 Session 自动归档**：登录凭据和对话记录保存在各 profile 独立的 Docker volume（`blackhole-claude-home-<profile>`）中，并通过 `SessionStart` / `SessionEnd` hook 自动记录 `profile <-> session <-> 宿主机路径` 映射。
- **剪贴板贴图直通与沙盒标识**：终端粘贴截图时写入宿主机 `$TMPDIR` 的临时图片会以相同绝对路径只读镜像进容器；同时通过启动横幅与 Claude 底部常驻的紫底 `⬤ BLACKHOLE` 徽标，一眼区分隔离容器与宿主机终端。

## 使用

> **推荐用法：交给 Agent 驾驭。** 你不需要死记硬背下面的所有子命令。直接让宿主机上的 Coding Agent 读取本仓库（内置了 [`AGENTS.md`](AGENTS.md) 操作手册），由它代你完成环境初始化、Colima profile 目录绑定、编写项目的 `.blackhole/Dockerfile`、开启 `net-watch` 断网保护以及导出或迁移历史会话。

在 `$HOME` 下的任意项目目录中直接运行 `claude`：

```bash
$ cd ~/Code/my-app
$ claude
 ⬤ BLACKHOLE  Colima VM isolated session · default
/Users/you/Code/my-app (only this directory is mounted)
```

所有原生参数都会直接透传给虚拟机内的 Claude Code：

```bash
claude -p "explain this repository"
claude --resume
```

在另一个终端随时检查启动器接管状态、镜像版本和活跃容器：

```bash
$ blackhole status
Backend:        colima
Profile:        default
Claude launcher: protected (/Users/you/.local/bin/claude)
Colima:         running
Image:          blackhole-claude:latest
Image status:   built
Claude Code:    1.0.x (Claude Code)
Image ID:       sha256:...
Config volume:  blackhole-claude-home-default
Active sessions:
  blackhole-default-48210  /Users/you/Code/my-app
```

把项目目录绑定到指定的 Colima VM profile（子目录自动继承，绑定信息保存在仓库外的 `~/.blackhole/bindings/`）：

```bash
$ cd ~/Code/heavy-repo
$ blackhole bind work
blackhole: bound /Users/you/Code/heavy-repo -> profile 'work'
```

查看全部 VM profile、跨 VM 检索历史 session，并在任意目录下按 ID 前缀直接恢复会话：

```bash
$ blackhole profiles
PROFILE  STATUS   MEMORY  BLACKHOLE  PROJECTS  SESSIONS  LAST USED
default  Running  8GiB    yes        3         12        2026-11-08 11:20
work     Running  16GiB   yes        1         4         2026-11-08 12:05
scratch  Stopped  4GiB    -          -         -         -

$ blackhole sessions
PROFILE  SESSION                               PATH                        STARTED           LAST ACTIVE       STATUS
work     89e4f9ec-8960-4175-9bb0-4bbea4780fa9  /Users/you/Code/heavy-repo  2026-11-08 10:12  2026-11-08 12:05  ended

$ blackhole resume 89e4
blackhole: resuming 89e4f9ec-8960-4175-9bb0-4bbea4780fa9 on profile 'work' in /Users/you/Code/heavy-repo
```

如果需要进入完全相同的隔离容器环境手动排查或调试：

```bash
blackhole shell
```

## 安装

### macOS

依赖 **macOS**、**[Colima](https://github.com/abiosoft/colima)** 和 **Docker CLI**（`brew install colima docker`）。**宿主机无需安装 Claude Code 或 Node.js**（`blackhole setup` 会直接在虚拟机镜像内安装 `@anthropic-ai/claude-code`）。启动 Colima 时建议开启 Apple Virtualization（`vz`）和 `virtiofs` 以获得接近原生的文件读写性能：

```bash
colima start --vm-type vz --mount-type virtiofs --cpu 4 --memory 8 --disk 60
```

构建基础镜像并安装 `claude` 与 `blackhole` 软链接（默认安装到 `~/.local/bin`）：

```bash
git clone https://github.com/tommy0103/blackhole.git ~/Code/blackhole
cd ~/Code/blackhole
./bin/blackhole setup
./bin/blackhole install
```

确保 `~/.local/bin` 排在 `PATH` 的最前面：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

如需指定其他安装目录，或替换宿主机已有的 `claude` 可执行文件：

```bash
./bin/blackhole install --prefix /opt/homebrew/bin --force
```

卸载时只需删除软链接（`rm -f ~/.local/bin/claude ~/.local/bin/blackhole`），并按需清理 `~/.blackhole`、`blackhole-claude:latest` 镜像以及 `blackhole-claude-home-*` Docker volume。

### Windows（WSL2）与 Linux

启动器应在 **Linux/WSL2 终端内**运行。轻量路线是在已有的 WSL2 Ubuntu 中使用 [Docker Engine](https://docs.docker.com/engine/install/ubuntu/)。Docker Desktop 为可选项：如果已经安装，启用它的 [WSL2 集成](https://docs.docker.com/desktop/features/wsl/) 即可。不需要 Colima、嵌套虚拟机，也不需要在宿主机安装 Node.js 或 Claude Code。

先确保普通 WSL 用户可通过 `docker info` 访问 rootful Docker Engine（参见 Docker [安装后配置](https://docs.docker.com/engine/install/linux-postinstall/)）。目前不支持 rootless Docker：它把宿主用户的项目文件映射为容器 UID 0，导致 Blackhole 的非 root 会话用户无法编辑普通的 `0755` 目录和 `0644` 文件。启动器会在构建镜像或创建容器之前拒绝 rootless 会话；请切换到普通用户可访问的 rootful Docker context。避免用 `sudo` 运行启动器，以免改变主目录和 profile 状态。然后在 WSL 中执行：

```bash
docker info
mkdir -p ~/Code
git clone https://github.com/tommy0103/blackhole.git ~/Code/blackhole
cd ~/Code/blackhole
./bin/blackhole setup
./bin/blackhole install
export PATH="$HOME/.local/bin:$PATH"
cd ~/Code/my-app
claude
```

macOS 默认使用 `colima` 后端，Linux/WSL2 默认使用 `docker`，可通过 `BLACKHOLE_BACKEND` 显式覆盖。Docker profile 共享当前引擎和镜像缓存，各自使用独立的 `blackhole-claude-home-<profile>` 凭据/会话卷；`blackhole bind work` 不会创建虚拟机。启动器遵循已有的 Docker context 与 `DOCKER_HOST`。项目建议放在 `~/Code`，以获得更好的 [WSL 文件系统性能](https://learn.microsoft.com/en-us/windows/wsl/filesystems)；也支持 `/mnt/c/...` 路径、空格和中文。`.gitattributes` 可让 Windows 克隆时的可执行脚本保持 LF 换行。

| 能力 | macOS / Colima | Linux / WSL2 Docker |
| --- | --- | --- |
| 项目挂载、profile、会话、导出、自定义镜像 | 支持 | 支持，共享一个引擎 |
| 故障闭锁出网保护 | VM `net-watch` / `iptables` | 可选的仅代理内部网络，支持 `net off/on/status` |
| 剪贴板图片 | 自动镜像 macOS 临时文件 | 通过 Windows 联动按需执行 `clipboard paste` |

普通会话不新增 watcher 或 Docker 之外的常驻服务。仅代理会话使用同一镜像启动一个非特权 Node 转发进程，限制为 **64 MiB 内存和 0.25 CPU**，正常退出时自动删除。不申请 GPU。每个仅代理会话都有独立的内部网络，转发进程退出时一并删除空闲网络。若启动器被强制杀死，可能需要手动清理带标签的转发容器/会话容器。

`column` 是可选工具：未安装时，profile、绑定、项目和会话列表会回退为制表符分隔的输出。

## 命令与配置

### 子命令速查

| 命令 | 说明 |
| --- | --- |
| `claude [args...]` | 在所选后端的当前项目容器中启动 Claude Code |
| `blackhole setup [--claude-version VER]` | 构建或重建基础镜像（若存在 `.blackhole/Dockerfile` 则一并构建项目镜像） |
| `blackhole update [--claude-version VER]` | `setup` 的别名，用于更新到最新（或指定版本）的 Claude Code |
| `blackhole install [--prefix DIR] [--force]` | 在 `DIR`（默认 `~/.local/bin`）下创建 `claude` 和 `blackhole` 软链接 |
| `blackhole status` | 查看当前 profile、启动器接管状态、镜像 ID、FUSE 状态及活跃会话 |
| `blackhole shell` | 在当前项目的隔离容器环境内启动交互式 `bash` |
| `blackhole bind <profile> [dir]` | 将项目目录（及其子目录）绑定到指定的 profile |
| `blackhole bindings` / `blackhole unbind [dir]` | 查看所有目录绑定关系，或解除指定目录的绑定 |
| `blackhole profiles` | 列出 profile 及已归档的项目/会话数（Docker profile 共享引擎资源） |
| `blackhole projects` | 列出所有运行中 VM 的项目映射（`PROFILE`、`PATH`、`LAST USED`、`SESSIONS`） |
| `blackhole sessions` | 列出所有运行中 VM 的会话记录及实时状态（`active` / `ended` / `unknown`） |
| `blackhole resume [--profile P] [--dry-run] <prefix>` | 自动切换回会话记录的宿主机目录与 profile 并恢复该会话 |
| `blackhole export [--profile P] [-o FILE] <prefix>` | 将会话 JSONL 记录从 Docker volume 导出到宿主机 `~/.blackhole/sessions/<profile>/` |
| `blackhole export-watch start\|status\|stop` | 后台定时检测会话 JSONL 变化并原子更新到宿主机导出目录 |
| `blackhole net off\|on\|status [--all \| --profile P]` | Colima VM 断网，或 Docker profile 代理转发进程控制 |
| `blackhole net ipv6 off\|on\|status [--all \| --profile P]` | 通过 `sysctl` 与 `ip6tables` 禁用或恢复 VM 的 IPv6 |
| `blackhole net-watch start\|status\|stop` | 故障闭锁（fail-close）代理守护进程，宿主机 TUN/代理掉线时立即切断 VM 网络 |
| `blackhole clipboard start\|status\|stop\|disable\|enable` | 管理宿主机 `$TMPDIR` 截图镜像服务以支持终端粘贴图片 |
| `blackhole clipboard paste [relative.png]` | WSL2：把 Windows 剪贴板图片保存进项目，输出对应的 `/workspace/...` 路径 |
| `blackhole signal send --file PATH (--data JSON \| --message TEXT \| --stdin)` | 从宿主机原子写入信号文件，唤醒容器内正在等待的 `blackhole-signal wait` |

### 环境变量

| 变量名 | 默认值 | 说明 |
| --- | --- | --- |
| `BLACKHOLE_BACKEND` | `auto` | macOS 使用 `colima`，Linux/WSL2 使用 `docker`；也可显式选择 |
| `BLACKHOLE_PROFILE` | `default`（或目录绑定） | 通用 profile 覆盖参数，优先于 `BLACKHOLE_COLIMA_PROFILE` |
| `BLACKHOLE_COLIMA_PROFILE` | `default`（或目录绑定的 profile） | 覆盖 Colima profile 选择（优先级：环境变量 > 目录绑定 > `default`） |
| `BLACKHOLE_PROXY` | *(未设置)* | Docker 仅代理出网，格式为不带认证信息的 `http://host:port`；按 profile 保存，跨终端复用 |
| `BLACKHOLE_IMAGE` | `blackhole-claude:latest` | 强制使用指定容器镜像（同时跳过项目的 `.blackhole/Dockerfile`） |
| `BLACKHOLE_HOME_VOLUME` | `blackhole-claude-home-<profile>` | 自定义挂载到容器 `/home/node` 的 Docker volume 名称 |
| `BLACKHOLE_STATE_DIR` | `~/.blackhole` | 宿主机状态目录（存放目录绑定、导出的会话、剪贴板镜像及守护进程状态） |
| `BLACKHOLE_CLIPBOARD` | `1` | 设为 `0` 可在当前会话关闭 `$TMPDIR` 图片镜像与挂载 |
| `BLACKHOLE_FUSE` | `0` | 设为 `1` 为容器开启 `/dev/fuse` 支持（或在项目下创建 `.blackhole/fuse`） |
| `BLACKHOLE_ALLOW_BROAD_MOUNT` | `0` | 设为 `1` 绕过安全拦截，允许直接挂载 `/`、`/Users` 或 `$HOME` |
| `ANTHROPIC_API_KEY` | *(不传递)* | 仅在宿主机已导出该变量时透传进容器 |
| `CLAUDE_CODE_OAUTH_TOKEN` | *(不传递)* | 仅在宿主机已导出该变量时透传进容器 |
| `NO_COLOR` | *(不传递)* | 禁用启动横幅、status line 与 Shell 提示符的 ANSI 颜色 |

### 项目级 Linux 工具镜像

宿主机 `/opt/homebrew/bin` 下的 macOS Mach-O 二进制无法在 Linux VM 内直接运行。如果项目需要额外的 Linux CLI 工具或编译依赖，可以在项目目录下创建 `.blackhole/Dockerfile`：

```dockerfile
ARG BLACKHOLE_BASE=blackhole-claude:latest
FROM ${BLACKHOLE_BASE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gh jq ffmpeg libfuse3-dev \
    && rm -rf /var/lib/apt/lists/*
```

随后运行 `blackhole setup`（或直接运行 `claude`，缺失项目镜像时会自动触发构建）。Docker 构建上下文仅包含 `.blackhole/` 目录，不会把整个项目源码传给 daemon；镜像 tag（`blackhole-claude:project-<hash>`）由项目路径与 Dockerfile 内容哈希共同决定。

### 网络控制与代理断网保护

通过虚拟机内的 `BLACKHOLE_AIRGAP` `iptables`/`ip6tables` 链，可以随时切断或恢复运行中 VM 的外网访问（保留 loopback、Docker 内网 `172.16.0.0/12` 及宿主机网关，断网期间 `colima ssh` 和 `docker` 依然可用）：

```bash
blackhole net off --all
blackhole net status
blackhole net on --all
blackhole net ipv6 off --all
```

如果宿主机通过 Shadowrocket、Surge 或 Clash 等 TUN 代理出网，希望在代理意外退出时立即切断 VM 网络防止直连泄漏，可以启动 `net-watch`：

```bash
blackhole net-watch start
blackhole net-watch status
blackhole net-watch stop   # 停止后保持断网（fail-close）；需手动运行 'blackhole net on --all' 恢复
```

`net-watch` 采用 **fail-close（故障闭锁）** 策略：启动时先切断 VM 网络，随后探测 `https://www.google.com/generate_204`（默认 `--interval 1`、`--fail-after 2`、`--recover-after 2`、`--timeout 15`，并校验默认路由是否经过 `utun*` 网卡）；同时会拉起独立的 `__net-guard` 进程监控心跳，即使主进程卡死或被 `SIGKILL` 杀掉，guard 也会立即锁死所有 VM 网络。非 TUN 模式的 HTTP 代理可传入 `--no-require-tun --proxy URL --url URL`。

#### Linux / WSL2 仅代理出网

Docker 后端需要显式指定一个**容器内可访问**的 HTTP 代理，例如 Windows 代理软件的 HTTP/mixed 端口。HTTP 代理可通过 CONNECT 隧道访问 HTTPS 目标。目前仅支持不带认证信息的 `http://host:port`。WSL2 NAT 模式下运行原生 Docker 时，Windows 宿主机通常是 WSL 默认网关；代理需要监听 WSL 可以访问的地址。Docker Desktop 的 `host.docker.internal` 指向宿主机；原生 Engine 下该名称映射到 Docker 宿主机（WSL 自身），不一定是 Windows。请根据网络模式确认地址，Blackhole 不会修改 Windows 代理设置或防火墙规则。

```bash
# WSL2 NAT 模式下原生 Docker 的示例；请按实际情况修改端口。
export BLACKHOLE_PROXY="http://$(ip route show default | awk '{print $3; exit}'):7890"
claude
# 在同一绑定项目/profile 的另一个终端中：
blackhole net status
blackhole net off
blackhole net on
```

Claude 只接入 Docker `--internal` 网络。一个非特权转发进程把 TCP 流量转发到指定代理，不挂载项目或凭据，也不发布宿主机端口。`HTTP_PROXY`/`HTTPS_PROXY` 及其小写变量指向该转发进程。不遵循这些变量的程序无法访问外网。代理或转发进程停止后，容器无法回退到直接访问外网，因此无需 TUN 轮询或心跳进程。最终出口 IP 仍由上游代理自身的路由规则决定；这一功能不提供账号匿名化，也不是对抗恶意内核的沙盒。

受保护的会话显式使用容器本地 DNS 上游（`127.0.0.1`），保留 Docker 对本地转发进程别名的解析，同时避免受 [CVE-2024-29018](https://github.com/moby/moby/security/advisories/GHSA-mq39-4gv4-mvpx) 影响的旧版引擎通过宿主机转发 DNS 查询。转发进程仍正常解析已配置的上游地址。

代理地址保存在 `~/.blackhole/docker-proxy/<profile>.proxy`，新终端也会沿用，避免意外直连。`net off` 只停止该 profile 的转发进程，并让后续会话保持断网；`net on` 可重新启动。两者均不修改其他 WSL 服务或 Docker 容器的网络。已经启动的普通会话需要先用代理模式重启，才能受到网络控制保护。状态检查会读取运行中的容器；保护不完整时显示 `unguarded-sessions` 或 `BLOCK-FAILED`，不会误报隔离成功。`--all` 会尝试控制全部已配置的 profile，只要有 profile 无法控制就返回错误。`net-watch` 和 VM 级 `net ipv6` 仍仅适用于 Colima。如果明确要恢复某 profile 的普通 Docker 网络，应先结束其会话，再删除对应的 `.proxy` 和 `.proxy.blocked` 状态文件。

Docker 内部网络仍允许访问网关地址上的宿主机服务，具体边界见 [Docker 网络文档](https://docs.docker.com/reference/cli/docker/network/create/#network-internal-mode---internal)；它不会隔离所有本地宿主机服务。

#### Windows 剪贴板截图

按 **Win+Shift+S** 截图后，在同一项目的另一个 WSL 终端中执行：

```bash
blackhole clipboard paste
# /workspace/.blackhole/clipboard/20261110-120000-1234.png
# 把输出路径粘贴给 Claude，也可以指定相对路径：
blackhole clipboard paste images/screenshot.png
```

命令只调用一次 STA 模式的 Windows PowerShell，经 .NET 返回 PNG 数据，Windows 端不打开项目路径。Python 辅助程序通过固定的目录句柄保存完整图片，并用原子硬链接拒绝已有目标，也能防止并发替换符号链接导致越界。父目录符号链接和目录穿越均会被拒绝。命令不改变 Windows 剪贴板。需要启用 WSL interop，并提供 `powershell.exe`、`wslpath`、Python 3 和支持硬链接的文件系统。不进行后台剪贴板轮询或自动注入 TUI 按键。没有 Windows 联动能力的 Linux 环境可以直接把图片放入项目。

### 跨进程文件信号

Claude Code 没有向空闲 TUI 异步推送消息的官方接口。当宿主机上的外部进程（如 Codex 或测试脚本）需要与容器内的 Claude 协同工作时，可以通过项目目录内的信号文件完成同步：

```bash
# 容器内（Claude）：阻塞等待 .blackhole/signals/tests.json 创建或更新
blackhole-signal wait --file .blackhole/signals/tests.json --timeout 1800

# 宿主机（外部工具）：原子写入信号内容并唤醒 Claude
blackhole signal send --file .blackhole/signals/tests.json \
  --data '{"status":"passed","summary":"42 tests passed"}'
```

`blackhole-signal wait` 会在文件内容稳定一个轮询周期后返回（默认 `--interval 500` 毫秒，支持 `--since <epoch|ISO>`，单文件上限 1 MiB；退出码：`0` 收到信号、`1` 错误、`124` 超时）。请将信号内容视作不可信数据，切勿直接 `eval`。

### 跨 Profile 迁移会话

会话 JSONL 保存在各 profile 的 Docker volume 内（`/home/node/.claude/projects/-workspace/<session-id>.jsonl`）。在不同 profile 之间迁移凭据与会话时，请在 `$HOME` 下创建中转目录（Colima 默认不挂载 `/tmp`）：

```bash
stage="$HOME/.cache/blackhole-migrate" && mkdir -p "$stage"
DOCKER_HOST="unix://$HOME/.colima/default/docker.sock" docker run --rm \
  -v blackhole-claude-home-default:/home/node:ro -v "$stage:/backup" \
  --entrypoint tar blackhole-claude:latest -C /home/node -cf /backup/session.tar \
  .claude/.credentials.json .claude/projects/-workspace/<session-id>.jsonl
DOCKER_HOST="unix://$HOME/.colima/work/docker.sock" docker volume create blackhole-claude-home-work
DOCKER_HOST="unix://$HOME/.colima/work/docker.sock" docker run --rm \
  -v blackhole-claude-home-work:/home/node -v "$stage:/backup:ro" \
  --entrypoint tar blackhole-claude:latest -C /home/node -xf /backup/session.tar
rm -rf "$stage"
```

## 限制与边界

### 挂载与隔离边界

每次通过 `claude` 启动的容器默认启用 `--security-opt no-new-privileges` 和 `--pids-limit 512`，最多包含以下三项挂载（剪贴板镜像仅适用于 macOS）：

| 宿主机 / Volume 来源 | 容器内路径 | 权限 | 用途 |
| --- | --- | --- | --- |
| `$PWD` | `/workspace` | `rw` | 项目实时 bind mount（Colima 使用 `virtiofs`） |
| `blackhole-claude-home-<profile>` | `/home/node` | `rw` | Claude 登录凭据、设置与会话注册表 |
| `~/.blackhole/clipboard` | 宿主机 `$TMPDIR` 同名路径 | `ro` | 终端剪贴板粘贴截图只读镜像 |

- **文件系统级隔离，而非对抗恶意内核的沙盒**：未执行 `blackhole net off` 时，容器具备访问 Anthropic API 的外网权限。VM 内的 `iptables` 断网规则（`net off` / `net-watch`）属于防误连开关，VM 内拥有 root 权限的进程可自行清除。请勿将不可信镜像设为 `BLACKHOLE_IMAGE`。
- **FUSE 模式会降低隔离强度**：开启 FUSE（`touch .blackhole/fuse` 或 `BLACKHOLE_FUSE=1`）会移除 `no-new-privileges` 并附加 `--device /dev/fuse --cap-add SYS_ADMIN --security-opt apparmor=unconfined`，以便 `fusermount3` 在容器内挂载文件系统，请仅对可信项目启用。
- **剪贴板贴图范围与延迟**：仅镜像宿主机 `$TMPDIR` 下新生成的图片文件（`png`、`jpg`、`jpeg`、`gif`、`webp`、`heic`、`tiff`、`bmp`，每 `0.5s` 轮询一次，7 天后自动清理）。从 `~/Desktop` 或 `~/Downloads` 直接拖入终端的文件不会挂载进容器，需先拷入项目目录。
- **同 Profile 共享配置卷**：在同一 Colima profile 下并发运行多个会话会共享同一个 `/home/node` volume，配置写入可能相互覆盖。修改 `bin/blackhole` 或通过 `blackhole setup` 重建镜像只会对新启动的会话生效，不会影响已经在运行的容器。

### virtiofs 性能实测

以下是在 Apple Silicon macOS 上的实测结果，对比宿主机原生 `APFS`、容器 `/workspace`（`virtiofs` 共享挂载）以及容器 `/tmp`（VM 内部 `ext4` 虚拟磁盘）：

| 测试负载 | 宿主机原生 (`APFS`) | 容器 `/workspace` (`virtiofs`) | 容器 `/tmp` (`ext4`) | 说明 |
| --- | :---: | :---: | :---: | --- |
| **64 MB 顺序写 (`fsync`)** | `16.4 ms` (3.9 GB/s) | **`53.7 ms` (1.2 GB/s)** | `37.4 ms` (1.7 GB/s) | GB/s 级吞吐，大文件读写无瓶颈 |
| **64 MB 顺序读** | `5.0 ms` (12.8 GB/s) | **`20.1 ms` (3.2 GB/s)** | `3.6 ms` (17.7 GB/s) | 接近原生的缓存读取速度 |
| **创建 1,000 个 1 KB 文件** | `58.4 ms` | **`196.0 ms`** (0.19 ms/个) | `15.4 ms` | 每千个小文件耗时约 ~0.2 秒 |
| **`stat` 遍历 1,000 个文件** | `4.7 ms` | **`33.9 ms`** (0.03 ms/个) | `3.6 ms` | 扫描万级文件代码库仅需 ~0.3 秒 |
| **读取 1,000 个 1 KB 文件** | `10.4 ms` | **`126.3 ms`** (0.12 ms/个) | `5.1 ms` | `rg` 搜索与代码读写体感无异 |
| **删除 1,000 个 1 KB 文件** | `35.2 ms` | **`109.2 ms`** (0.11 ms/个) | `5.3 ms` | 每千个小文件删除约 ~0.1 秒 |

对于会产生数万个临时文件的重度构建（如 Rust `cargo build`），可以将构建缓存目录指向容器内的 `/tmp`（例如 `CARGO_TARGET_DIR=/tmp/target`），既能享受 VM 原生 `ext4` 的编译速度，又能保持 `/workspace` 下源码实时同步。

## 许可证

MIT — 详见 [LICENSE](LICENSE)。
