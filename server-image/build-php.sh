#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Builds the PHP runtime that GenisysPro / LiteCore 1.1.5 needs:
#   PHP 7.0 (ZTS) + pthreads + yaml, installed into /opt/php
#
# Why exactly PHP 7.0 and nothing newer:
#   Genisys-era code declares a class named `Void`
#   (pocketmine\level\generator\Void, imported in src/pocketmine/Server.php).
#   PHP 7.1 turned `void` into a reserved word, so on 7.1+ the phar dies with
#     Fatal error: Cannot use pocketmine\level\generator\Void as Void
#     because 'Void' is a special class name
#   7.0.33 is the final PHP 7.0 release, and pthreads v3.1.6 is the last
#   pthreads release that supports it (Genisys requires pthreads >= 3.1.5).
#
# pmmp/PHP-Binaries is deliberately not used: upstream deleted its php-7.x
# branches and the current scripts build PHP 8 only.
# ---------------------------------------------------------------------------
set -euo pipefail

PREFIX="/opt/php"
WORK="${WORK:-/build}"
PHP_VERSION="${PHP_VERSION:-7.0.33}"
PHP_TARBALL_URL="${PHP_TARBALL_URL:-}"
PTHREADS_REF="${PTHREADS_REF:-v3.1.6}"
YAML_VERSION="${YAML_VERSION:-2.0.4}"
OPENSSL_VERSION="${OPENSSL_VERSION:-1.0.2u}"
JOBS="${JOBS:-$(nproc)}"
OPENSSL_ARG="--with-openssl"

log()  { printf '\n==> %s\n' "$*" >&2; }
warn() { printf '!!! %s\n' "$*" >&2; }
die()  { printf 'FATAL: %s\n' "$*" >&2; exit 1; }

fetch() {
	local out="$1"
	shift
	local url
	for url in "$@"; do
		log "downloading $url"
		if curl -fsSL --retry 3 --retry-delay 2 "$url" -o "$out"; then
			return 0
		fi
		warn "download failed: $url"
	done
	die "could not download $out"
}

# --- shortcut: ready-made PHP 7 tarball ------------------------------------
install_prebuilt() {
	log "installing prebuilt PHP from $PHP_TARBALL_URL"
	mkdir -p "$WORK/prebuilt" "$PREFIX"
	fetch "$WORK/php-prebuilt.tar.gz" "$PHP_TARBALL_URL"
	tar -xzf "$WORK/php-prebuilt.tar.gz" -C "$WORK/prebuilt"
	local bin
	bin="$(find "$WORK/prebuilt" -type f -name php -perm -u+x | head -n 1)"
	[ -n "$bin" ] || die "no php binary inside $PHP_TARBALL_URL"
	cp -a "$(dirname "$(dirname "$bin")")"/. "$PREFIX"/
}

# --- OpenSSL ---------------------------------------------------------------
# PHP 7.0 cannot build against OpenSSL >= 1.1. On old distros (debian:stretch)
# the system library is 1.0.2 and we just use it; anywhere else we compile
# OpenSSL 1.0.2 into $PREFIX/openssl and burn an rpath into the PHP binary so
# no LD_LIBRARY_PATH is needed at runtime.
prepare_openssl() {
	local version
	version="$(pkg-config --modversion openssl 2>/dev/null || echo unknown)"
	case "$version" in
	1.0.*)
		log "using system OpenSSL $version"
		return 0
		;;
	esac

	log "system OpenSSL is $version - compiling OpenSSL $OPENSSL_VERSION for PHP 7.0"
	rm -rf "$WORK/openssl"
	mkdir -p "$WORK/openssl"
	fetch "$WORK/openssl.tar.gz" \
		"https://www.openssl.org/source/old/1.0.2/openssl-$OPENSSL_VERSION.tar.gz" \
		"https://ftp.openssl.org/source/old/1.0.2/openssl-$OPENSSL_VERSION.tar.gz"
	tar -xzf "$WORK/openssl.tar.gz" -C "$WORK/openssl" --strip-components=1
	cd "$WORK/openssl"
	./config --prefix="$PREFIX/openssl" --openssldir="$PREFIX/openssl/ssl" shared zlib -fcommon
	make # OpenSSL 1.0.2 does not build reliably in parallel
	make install_sw

	OPENSSL_ARG="--with-openssl=$PREFIX/openssl"
	export PKG_CONFIG_PATH="$PREFIX/openssl/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
	export LDFLAGS="-Wl,-rpath,$PREFIX/openssl/lib ${LDFLAGS:-}"
}

# --- PHP -------------------------------------------------------------------
compile_php() {
	rm -rf "$WORK/php-src"
	mkdir -p "$WORK/php-src"
	fetch "$WORK/php.tar.gz" \
		"https://www.php.net/distributions/php-$PHP_VERSION.tar.gz" \
		"https://museum.php.net/php7/php-$PHP_VERSION.tar.gz" \
		"https://github.com/php/php-src/archive/refs/tags/php-$PHP_VERSION.tar.gz"
	tar -xzf "$WORK/php.tar.gz" -C "$WORK/php-src" --strip-components=1
	cd "$WORK/php-src"
	[ -x ./configure ] || ./buildconf --force

	# Debian multiarch hides gmp.h, PHP 7.0 only looks in /usr/include
	if [ ! -e /usr/include/gmp.h ]; then
		ln -sf /usr/include/*-linux-gnu/gmp.h /usr/include/gmp.h 2>/dev/null || true
	fi

	export CFLAGS="-O2 -fcommon ${CFLAGS:-}"
	export CXXFLAGS="-O2 -fcommon ${CXXFLAGS:-}"

	local required=(
		"--prefix=$PREFIX"
		--enable-maintainer-zts
		--enable-cli
		--disable-cgi
		--disable-phpdbg
		--disable-fpm
		--without-pear
		--enable-sockets
		--enable-bcmath
		--enable-mbstring
		--enable-pcntl
		--enable-zip
		"$OPENSSL_ARG"
		--with-curl
		--with-zlib
		--with-sqlite3
		--with-pdo-sqlite
	)
	local optional=(--with-gmp --with-readline)

	log "configuring PHP $PHP_VERSION (ZTS)"
	if ! ./configure "${required[@]}" "${optional[@]}"; then
		warn "configure failed with optional extensions - retrying without gmp/readline"
		make distclean >/dev/null 2>&1 || true
		./configure "${required[@]}" || die "PHP configure failed"
	fi

	log "compiling PHP $PHP_VERSION with $JOBS jobs (this is the slow part)"
	make -j"$JOBS"
	make install
}

# --- extensions ------------------------------------------------------------
build_extension() {
	local dir="$1"
	shift
	cd "$dir"
	"$PREFIX/bin/phpize"
	./configure --with-php-config="$PREFIX/bin/php-config" "$@"
	make -j"$JOBS"
	make install
}

compile_pthreads() {
	log "building pthreads for PHP $PHP_VERSION"
	rm -rf "$WORK/pthreads"
	git clone --quiet https://github.com/krakjoe/pthreads.git "$WORK/pthreads"
	cd "$WORK/pthreads"
	local ref found=""
	for ref in "$PTHREADS_REF" v3.1.6 v3.1.5; do
		if git checkout --quiet "$ref" 2>/dev/null; then
			found="$ref"
			log "pthreads checked out at $ref"
			break
		fi
		warn "pthreads ref not found: $ref"
	done
	[ -n "$found" ] || warn "using pthreads default branch - may not support PHP 7.0"
	build_extension "$WORK/pthreads" --enable-pthreads
}

compile_yaml() {
	log "building pecl yaml $YAML_VERSION"
	rm -rf "$WORK/yaml"
	mkdir -p "$WORK/yaml"
	fetch "$WORK/yaml.tgz" "https://pecl.php.net/get/yaml-$YAML_VERSION.tgz"
	tar -xzf "$WORK/yaml.tgz" -C "$WORK/yaml" --strip-components=1
	build_extension "$WORK/yaml" --with-yaml
}

write_ini() {
	local ext_dir
	ext_dir="$("$PREFIX/bin/php-config" --extension-dir)"
	log "writing $PREFIX/bin/php.ini (extension_dir=$ext_dir)"
	cat >"$PREFIX/bin/php.ini" <<INI
extension_dir="$ext_dir"
extension=pthreads.so
extension=yaml.so
memory_limit=768M
phar.readonly=0
date.timezone=UTC
zend.assertions=-1
INI
}

verify() {
	log "verifying the build"
	PHPRC="$PREFIX/bin" "$PREFIX/bin/php" -v
	PHPRC="$PREFIX/bin" "$PREFIX/bin/php" -m >"$PREFIX/modules.txt"
	cat "$PREFIX/modules.txt"

	grep -qix pthreads "$PREFIX/modules.txt" ||
		die "pthreads is missing - GenisysPro / LiteCore will not start"

	local ext
	for ext in yaml sockets curl openssl bcmath mbstring zip sqlite3 zlib; do
		grep -qix "$ext" "$PREFIX/modules.txt" || warn "extension missing: $ext"
	done

	local branch
	branch="$(PHPRC="$PREFIX/bin" "$PREFIX/bin/php" -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;')"
	if [ "$branch" != "7.0" ]; then
		warn "PHP $branch is not 7.0 - Genisys class 'Void' is rejected by PHP 7.1+"
	fi
}

main() {
	mkdir -p "$WORK" "$PREFIX"
	cd "$WORK"

	if [ -n "$PHP_TARBALL_URL" ]; then
		install_prebuilt
	else
		prepare_openssl
		compile_php
		compile_pthreads
		compile_yaml
		write_ini
	fi

	verify
	log "PHP is ready in $PREFIX"

	cd /
	rm -rf "$WORK/php-src" "$WORK/pthreads" "$WORK/yaml" "$WORK/openssl" \
		"$WORK/prebuilt" "$WORK"/*.tar.gz "$WORK"/*.tgz /tmp/php* || true
}

main "$@"
