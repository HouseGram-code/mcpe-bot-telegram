# syntax=docker/dockerfile:1
#
# GenisysPro (PocketMine-MP fork) / LiteCore 1.1.5 - MCPE 1.1.5
#
# Stage 1 builds the PHP 7 runtime with pthreads that old PocketMine forks need.
# By default it compiles pmmp/PHP-Binaries (branch php-7.2). If you already have
# a prebuilt tarball, pass PHP_TARBALL_URL and the build skips compilation:
#
#   docker build --build-arg PHP_TARBALL_URL=https://.../PHP-7.2-Linux-x86_64.tar.gz .
#
ARG PHP_BRANCH=php-7.2

FROM debian:bullseye AS php-builder
ARG PHP_BRANCH
ARG PHP_TARBALL_URL=""
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf automake libtool libtool-bin m4 make cmake gcc g++ \
        bison re2c file pkg-config git curl wget ca-certificates \
        tar xz-utils bzip2 gzip patch python3 libc6-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN set -eux; \
    if [ -n "$PHP_TARBALL_URL" ]; then \
        echo "==> downloading prebuilt PHP: $PHP_TARBALL_URL"; \
        curl -fsSL "$PHP_TARBALL_URL" -o php.tar.gz; \
        mkdir -p /tmp/php && tar -xzf php.tar.gz -C /tmp/php; \
        PHP_BIN="$(find /tmp/php -type f -name php -perm -u+x | head -n 1)"; \
        test -n "$PHP_BIN"; \
        PHP_ROOT="$(dirname "$(dirname "$PHP_BIN")")"; \
    else \
        echo "==> compiling PHP from pmmp/PHP-Binaries ($PHP_BRANCH)"; \
        git clone --depth 1 -b "$PHP_BRANCH" https://github.com/pmmp/PHP-Binaries.git .; \
        ./compile.sh -t linux64 -j"$(nproc)"; \
        PHP_ROOT="/build/bin/php7"; \
    fi; \
    mkdir -p /opt/php; \
    cp -a "$PHP_ROOT"/. /opt/php/; \
    EXT_DIR="$(find /opt/php/lib -maxdepth 4 -type d -name 'no-debug-*' | head -n 1)"; \
    if [ -n "$EXT_DIR" ] && [ -f /opt/php/bin/php.ini ]; then \
        sed -i "s|^extension_dir=.*|extension_dir=\"$EXT_DIR\"|" /opt/php/bin/php.ini; \
    fi; \
    PHPRC=/opt/php/bin /opt/php/bin/php -v; \
    PHPRC=/opt/php/bin /opt/php/bin/php -m > /opt/php/modules.txt; \
    if ! grep -qi pthreads /opt/php/modules.txt; then \
        echo "WARNING: pthreads extension missing - GenisysPro may refuse to start"; \
    fi


FROM debian:bullseye-slim AS runtime
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates tini tzdata procps \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 --shell /bin/bash mc

COPY --from=php-builder /opt/php /opt/php

ENV PATH="/opt/php/bin:${PATH}" \
    PHPRC="/opt/php/bin" \
    DATA_DIR="/data" \
    SERVER_PORT="19132" \
    SERVER_NAME="GenisysPro" \
    MOTD="GenisysPro 1.1.5" \
    MAX_PLAYERS="20" \
    GAMEMODE="0" \
    DIFFICULTY="2" \
    LEVEL_NAME="world" \
    PHP_MEMORY_LIMIT="768M" \
    TERM="dumb"

COPY GenisysPro.phar /opt/genisys/GenisysPro.phar
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /data \
 && chown -R mc:mc /data /opt/genisys

USER mc
WORKDIR /data
VOLUME ["/data"]
EXPOSE 19132/udp

# tini forwards SIGTERM so the server saves the world on `docker stop`.
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/entrypoint.sh"]
