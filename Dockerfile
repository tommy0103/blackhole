FROM node:22-bookworm-slim

ARG CLAUDE_CODE_VERSION=latest

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        fuse3 \
        git \
        gosu \
        less \
        openssh-client \
        procps \
        ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g --include=optional "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force

COPY docker-entrypoint.sh /usr/local/bin/blackhole-entrypoint
COPY blackhole-statusline.sh /usr/local/bin/blackhole-statusline
COPY blackhole-session-hook.sh /usr/local/bin/blackhole-session-hook
COPY blackhole-registry.mjs /usr/local/bin/blackhole-registry
COPY blackhole-signal.mjs /usr/local/bin/blackhole-signal
COPY blackhole-proxy.cjs /usr/local/bin/blackhole-proxy
COPY blackhole-bashrc.sh /usr/local/etc/blackhole-bashrc
RUN chmod 0755 /usr/local/bin/blackhole-entrypoint \
        /usr/local/bin/blackhole-statusline \
        /usr/local/bin/blackhole-session-hook \
        /usr/local/bin/blackhole-registry \
        /usr/local/bin/blackhole-signal

ENV HOME=/home/node \
    SHELL=/bin/bash
WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/blackhole-entrypoint"]
CMD ["claude"]
