#!/usr/bin/env bash
# zapret-discord-arch — полное удаление всего, что ставит ./install.sh.
#
#   ./uninstall.sh              GUI (пакет zapret-discord-arch / старый zapret-discord-gui / --direct файлы)
#                               + адаптер /opt/zapret-discord-youtube-linux + сервис + таблица nftables
#                               + zapret2, если он ставился нашим --zapret2 (в /opt/zapret2/config наш блок)
#   ./uninstall.sh --gui-only   только GUI
#   ./uninstall.sh --keep-gui   только zapret, GUI оставить
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RM_GUI=1; RM_ZAPRET=1
case "${1:-}" in
    --gui-only) RM_ZAPRET=0 ;;
    --keep-gui) RM_GUI=0 ;;
    "") ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) echo "Неизвестный параметр: $1" >&2; exit 1 ;;
esac
msg() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
if [[ $EUID -eq 0 ]]; then SUDO=(); else SUDO=(sudo); fi

if (( RM_ZAPRET )); then
    msg "Удаляю адаптер zapret (вариант A)"
    "${SUDO[@]}" bash "$HERE/scripts/zapret-uninstall.sh"
    if pacman -Qq zapret2 >/dev/null 2>&1 && grep -qs '^# --- zapret2-discord BEGIN ---$' /opt/zapret2/config; then
        msg "Удаляю zapret2 (ставился через ./install.sh --zapret2)"
        "${SUDO[@]}" bash "$HERE/scripts/zapret-uninstall.sh" --zapret2
    fi
fi

if (( RM_GUI )); then
    msg "Удаляю GUI"
    for p in zapret-discord-arch zapret-discord-gui; do
        pacman -Qq "$p" >/dev/null 2>&1 && "${SUDO[@]}" pacman -R --noconfirm "$p"
    done
    for d in zapret-discord-arch zapret-discord-gui; do
        "${SUDO[@]}" rm -rf "/usr/lib/$d" "/usr/share/$d" "/usr/share/licenses/$d"
    done
    "${SUDO[@]}" rm -f /usr/bin/zapret-discord-gui \
        /usr/share/polkit-1/actions/io.github.zapret_discord_gui.policy \
        /usr/share/applications/io.github.zapret_discord_gui.desktop \
        /usr/share/icons/hicolor/scalable/apps/zapret-discord-gui.svg
    "${SUDO[@]}" gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
    "${SUDO[@]}" update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi
msg "Готово. Пакеты-зависимости (git, nftables, gtk4 …) не удалялись — они могли понадобиться не только нам."
