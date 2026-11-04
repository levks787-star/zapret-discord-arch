#!/usr/bin/env bash
# zapret-discord-arch — диагностика: сервис, nfqws, nftables, доступность Discord, наличие GUI.
#   ./check.sh      (sudo спросится сам — нужен для чтения таблиц nftables)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ $EUID -ne 0 ]] && command -v sudo >/dev/null; then
    sudo bash "$HERE/scripts/check.sh"
else
    bash "$HERE/scripts/check.sh"
fi
printf '\n\033[1m%s\033[0m\n' "5. GUI"
if q="$(pacman -Q zapret-discord-arch 2>/dev/null)"; then
    printf '  \033[1;32m[OK]\033[0m %s\n' "$q (zapret-discord-gui)"
elif [[ -x /usr/bin/zapret-discord-gui ]]; then
    printf '  \033[1;32m[OK]\033[0m %s\n' "установлен файлами (--direct или старая версия)"
else
    echo "  GUI не установлен: ./install.sh --gui-only"
fi
