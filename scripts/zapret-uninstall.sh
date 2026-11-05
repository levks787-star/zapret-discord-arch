#!/usr/bin/env bash
# Ядро удаления zapret (root). Обычно вызывается из ../uninstall.sh.
#   sudo scripts/zapret-uninstall.sh            - вариант A (адаптер)
#   sudo scripts/zapret-uninstall.sh --zapret2  - вариант B (удаление пакета zapret2)
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "запустите через sudo"; exit 1; }
ADAPTER_DIR="/opt/zapret-discord-youtube-linux"

if [[ ${1:-} == --zapret2 ]]; then
    systemctl disable --now zapret2.service 2>/dev/null || true
    pacman -Qq zapret2 >/dev/null 2>&1 && pacman -Rns --noconfirm zapret2
    nft delete table inet zapret2 2>/dev/null || true
    echo "zapret2 удалён (конфиг /opt/zapret2/config мог остаться как .pacsave)."
    exit 0
fi

if [[ -x $ADAPTER_DIR/service.sh ]]; then
    ( cd "$ADAPTER_DIR" && ./service.sh service remove ) || true
    ( cd "$ADAPTER_DIR" && ./service.sh kill ) || true
fi
nft delete table inet zapretunix 2>/dev/null || true
rm -rf "$ADAPTER_DIR"
echo "Адаптер удалён, правила nftables очищены."
