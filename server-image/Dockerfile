# syntax=docker/dockerfile:1
#
# GenisysPro (PocketMine-MP fork) / LiteCore 1.1.5 - MCPE 1.1.5
#
# This core is from 2017 and runs ONLY on PHP 7.0: it declares a class called
# `Void`, and PHP 7.1 made `void` a reserved word, so on 7.1+ the phar dies
# with "Cannot use pocketmine\level\generator\Void as Void because 'Void' is a
# special class name".
#
# debian:stretch is used on purpose - it is the last Debian whose OpenSSL
# (1.0.2) and libcurl still match PHP 7.0. build-php.sh compiles
# PHP 7.0 (ZTS) + pthreads + yaml; pmmp/PHP-Binaries is not used because
# upstream deleted its php-7.x branches and now builds PHP 8 only.
#
# Already have a ready-made PHP 7.0 tarball? Then nothing is compiled:
#
#   docker build --build-arg PHP_TARBALL_URL=https://.../PHP-7.0-Linux-x86_64.tar.gz .
#
ARG BASE_IMAGE=debian:stretch
ARG RUNTIME_IMAGE=debian:stretch-slim
ARG PHP_VERSION=7.0.33

FROM ${BASE_IMAGE} AS php-builder
ARG PHP_VERSION
ARG PHP_TARBALL_URL=""
ARG PTHREADS_REF=v3.1.6
ARG YAML_VERSION=2.0.4
ENV DEBIAN_FRONTEND=noninteractive

# stretch is archived, so apt has to be pointed at archive.debian.org
RUN set -eux; \
    if grep -qi stretch /etc/os-release; then \
        printf '%s\n' \
            'deb [check-valid-until=no] http://archive.debian.org/debian stretch main contrib' \
            'deb [check-valid-until=no] http://archive.debian.org/debian-security stretch/updates main contrib' \
            > /etc/apt/sources.list; \
    fi

RUN set -eux; \
    apt-get -o Acquire::Check-Valid-Until=false update; \
    apt-get install -y --no-install-recommends \
        build-essential autoconf automake libtool libtool-bin m4 \
        bison re2c file pkg-config git curl ca-certificates \
        tar xz-utils gzip patch \
        libxml2-dev zlib1g-dev libsqlite3-dev libyaml-dev libgmp-dev \
        libreadline-dev libedit-dev libbz2-dev; \
    apt-get install -y --no-install-recommends libssl1.0-dev \
        || apt-get install -y --no-install-recommends libssl-dev; \
    apt-get install -y --no-install-recommends libcurl4-openssl-dev \
        || apt-get install -y --no-install-recommends libcurl4-gnutls-dev; \
    if ! apt-get install -y --no-install-recommends tini; then :; fi; \
    if [ ! -x /usr/bin/tini ]; then \
        curl -fsSL -o /usr/bin/tini \
            https://github.com/krallin/tini/releases/download/v0.19.0/tini-static-amd64; \
        chmod +x /usr/bin/tini; \
    fi; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY build-php.sh /usr/local/bin/build-php.sh
RUN chmod +x /usr/local/bin/build-php.sh && /usr/local/bin/build-php.sh


FROM ${RUNTIME_IMAGE} AS runtime
ENV DEBIAN_FRONTEND=noninteractive

# shared libraries the compiled PHP 7.0 links against
RUN set -eux; \
    if grep -qi stretch /etc/os-release; then \
        printf '%s\n' \
            'deb [check-valid-until=no] http://archive.debian.org/debian stretch main contrib' \
            'deb [check-valid-until=no] http://archive.debian.org/debian-security stretch/updates main contrib' \
            > /etc/apt/sources.list; \
    fi; \
    apt-get -o Acquire::Check-Valid-Until=false update; \
    apt-get install -y --no-install-recommends \
        ca-certificates tzdata procps zlib1g libsqlite3-0 libyaml-0-2 \
        libgmp10 libxml2 libedit2; \
    apt-get install -y --no-install-recommends libssl1.0.2 \
        || apt-get install -y --no-install-recommends libssl1.1 || true; \
    apt-get install -y --no-install-recommends libcurl3 \
        || apt-get install -y --no-install-recommends libcurl4 || true; \
    apt-get install -y --no-install-recommends libreadline7 \
        || apt-get install -y --no-install-recommends libreadline8 || true; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --create-home --uid 1000 --shell /bin/bash mc

COPY --from=php-builder /usr/bin/tini /usr/bin/tini
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
