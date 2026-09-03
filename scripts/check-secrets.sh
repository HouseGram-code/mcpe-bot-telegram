#!/usr/bin/env bash
# Local secret scanner: the same thing GitHub push protection does, but before
# the commit even exists.
#
#   bash scripts/check-secrets.sh          # staged changes (used by the git hook)
#   bash scripts/check-secrets.sh all      # the whole working tree
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-staged}"
RED='\033[1;31m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

# "all" mode lists git-tracked files when this is a repo, otherwise the tree.
if git rev-parse --git-dir >/dev/null 2>&1; then HAVE_GIT=yes; else HAVE_GIT=no; fi

# Telegram bot token: <6-12 digits>:<35 chars>
TOKEN_RE='[0-9]{6,12}:[A-Za-z0-9_-]{35}'

list_files() {
	if [[ "$MODE" == "all" ]]; then
		if git rev-parse --git-dir >/dev/null 2>&1; then
			git ls-files
		else
			find . -type f -not -path './.git/*' -not -path './data/*' \
				-not -path './secrets/*' -not -path '*__pycache__*' -printf '%P\n'
		fi
	else
		git diff --cached --name-only --diff-filter=ACM
	fi
}

status=0
while IFS= read -r file; do
	[[ -z "$file" ]] && continue
	[[ -f "$file" ]] || continue
	case "$file" in
		.env.example) ;;
		.env | .env.*)
			# A local .env is fine - it is git-ignored. Being inside git is not.
			if [[ "$MODE" == "staged" || "$HAVE_GIT" == "yes" ]]; then
				printf "${RED}BLOCKED${NC} %s — в этом файле токен, его нельзя коммитить\n" "$file"
				status=1
			else
				printf "${GREEN}  ok${NC} %s есть локально и скрыт от git\n" "$file"
			fi
			continue
			;;
		secrets/.gitkeep) continue ;;
		secrets/*)
			printf "${RED}BLOCKED${NC} %s — секретный файл\n" "$file"
			status=1
			continue
			;;
		*.sqlite3 | *.sqlite3-* | *.zip)
			printf "${RED}BLOCKED${NC} %s — это данные рантайма, а не код\n" "$file"
			status=1
			continue
			;;
		*.phar | *.png | *.jpg | *.gz | *.zst) continue ;;
	esac
	if LC_ALL=C grep -qIE "$TOKEN_RE" -- "$file" 2>/dev/null; then
		printf "${RED}BLOCKED${NC} %s — похоже на токен Telegram:\n" "$file"
		LC_ALL=C grep -nIE "$TOKEN_RE" -- "$file" |
			head -n 3 |
			sed -E "s/${TOKEN_RE}/<TOKEN-MASKED>/g; s/^/        /"
		status=1
	fi
done < <(list_files)

if [[ $status -ne 0 ]]; then
	printf "\n${YELLOW}Как починить:${NC}\n"
	printf "  1. убери секрет из кода — токен должен быть только в .env\n"
	printf "     или в secrets/bot_token (BOT_TOKEN_FILE=/run/secrets/bot_token);\n"
	printf "  2. если токен уже где-то светился — /revoke у @BotFather.\n"
	exit 1
fi

printf "${GREEN}ok${NC} секретов в коде не найдено (проверено: %s)\n" "$MODE"
