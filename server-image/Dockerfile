# syntax=docker/dockerfile:1
#
# GenisysPro (PocketMine-MP fork) / LiteCore 1.1.5 - MCPE 1.1.5
#
# Stage 1 builds the PHP 7 runtime (ZTS + pthreads + yaml) that old PocketMine
# forks need. build-php.sh compiles PHP from php.net sources; pmmp/PHP-Binaries
# is not used any more because upstream deleted its php-7.x branches and the
# current scripts build PHP 8 only, which GenisysPro / LiteCore cannot run.
#
# Already have a ready-made PHP 7 tarball? Then nothing gets compiled:
#
#   docker build --build-arg PHP_TARBALL_URL=https://.../PHP-7.2-Linux-x86_64.tar.gz .
#
ARG PHP_VERSION=7.2.34

FROM debian:bullseye AS php-builder
ARG PHP_VERSION
ARG PHP_TARBALL_URL=""
ARG PTHREADS_REF=v3.2.0
ARG YAML_VERSION=2.0.4
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf automake libtool libtool-bin m4 make gcc g++ \
        bison re2c file pkg-config git curl wget ca-certificates \
        tar xz-utils bzip2 gzip patch libc6-dev \
        libxml2-dev libssl-dev libcurl4-openssl-dev zlib1g-dev \
        libsqlite3-dev libyaml-dev libgmp-dev libreadline-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY build-php.sh /usr/local/bin/build-php.sh
RUN chmod +x /usr/local/bin/build-php.sh && /usr/local/bin/build-php.sh


FROM debian:bullseye-slim AS runtime
ENV DEBIAN_FRONTEND=noninteractive

# tini for clean shutdowns + the shared libraries the compiled PHP links against
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates tini tzdata procps \
        libssl1.1 libcurl4 zlib1g libsqlite3-0 libyaml-0-2 libgmp10 \
        libreadline8 libedit2 libxml2 \
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
