#!/usr/bin/env bash
# zapret-discord-arch — единая установка: обход DPI для Discord (zapret) + GUI «Zapret Discord».
#
#   ./install.sh                       адаптер zapret (вариант A) + сервис + GUI (makepkg -> pacman)
#   ./install.sh --gui-only            только GUI (адаптер уже стоит или поставите кнопкой в окне)
#   ./install.sh --no-gui              только zapret (вариант A), без GUI
#   ./install.sh --zapret2             вариант B: AUR zapret2 + Discord-конфиг (GUI не ставится)
#   ./install.sh --strategy NAME       стратегия Flowseal (по умолчанию general.bat)
#   ./install.sh --iface NAME          сетевой интерфейс (по умолчанию any)
#   ./install.sh --no-service          не создавать systemd-сервис адаптера
#   ./install.sh --direct              GUI без makepkg: скопировать файлы в /usr
#
# Запускайте от обычного пользователя, БЕЗ sudo: пароль sudo спросится сам
# (makepkg от root не работает). Под Arch Linux и производные (EndeavourOS, Manjaro, CachyOS).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG=zapret-discord-arch
OLD_PKG=zapret-discord-gui
GUI_DEPS=(python python-gobject gtk4 libadwaita polkit iproute2 procps-ng nftables git curl grep sed bash)

DO_ZAPRET=1; DO_GUI=1; DIRECT=0; CORE_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --gui-only) DO_ZAPRET=0; shift ;;
        --no-gui)   DO_GUI=0; shift ;;
        --zapret2)  CORE_ARGS+=(--zapret2); DO_GUI=0; Z2=1; shift ;;
        --strategy|--iface)
            [[ $# -ge 2 && -n $2 ]] || { echo "$1 требует значение" >&2; exit 1; }
            CORE_ARGS+=("$1" "$2"); shift 2 ;;
        --no-service) CORE_ARGS+=(--no-service); shift ;;
        --direct)   DIRECT=1; shift ;;
        -h|--help)  sed -n 2,15p "$0"; exit 0 ;;
        *) echo "Неизвестный параметр: $1 (см. ./install.sh --help)" >&2; exit 1 ;;
    esac
done

msg()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==> ВНИМАНИЕ:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m==> ОШИБКА:\033[0m %s\n' "$*" >&2; exit 1; }

command -v pacman >/dev/null || die "pacman не найден — проект для Arch Linux и производных"
[[ ${Z2:-0} == 1 && $DO_ZAPRET == 0 ]] && die "--zapret2 и --gui-only несовместимы"
(( DO_ZAPRET || DO_GUI )) || die "--gui-only и --no-gui вместе: нечего ставить"

# Кто будет собирать пакет: makepkg нельзя от root.
BUILD_USER="$(id -un)"
if [[ $EUID -eq 0 ]]; then
    if [[ -n ${SUDO_USER:-} && $SUDO_USER != root ]]; then
        BUILD_USER="$SUDO_USER"
        warn "запущено через sudo — пакет GUI соберу от пользователя $BUILD_USER (в следующий раз просто ./install.sh)"
    elif (( DO_GUI && ! DIRECT )); then
        warn "запущено от root без SUDO_USER — GUI поставлю в режиме --direct (без makepkg)"
        DIRECT=1
    fi
    SUDO=()
else
    command -v sudo >/dev/null || die "нужен sudo: pacman -S sudo (от root) и добавьте себя в wheel"
    SUDO=(sudo)
fi
as_user() { if [[ $EUID -eq 0 ]]; then sudo -u "$BUILD_USER" "$@"; else "$@"; fi; }

install_zapret() {
    msg "Шаг 1/2: zapret (ядро установки scripts/zapret-install.sh)"
    "${SUDO[@]}" bash "$HERE/scripts/zapret-install.sh" "${CORE_ARGS[@]}"
}

remove_old_gui() {
    # прежние сборки под именем zapret-discord-gui (pacman или --direct)
    if pacman -Qq "$OLD_PKG" >/dev/null 2>&1; then
        msg "Удаляю старый пакет $OLD_PKG (заменяется на $PKG)"
        "${SUDO[@]}" pacman -R --noconfirm "$OLD_PKG" || warn "не удалось удалить $OLD_PKG — продолжаю"
    fi
    if [[ -e /usr/lib/$OLD_PKG ]] && ! pacman -Qo "/usr/lib/$OLD_PKG" >/dev/null 2>&1; then
        msg "Удаляю старую direct-установку /usr/lib/$OLD_PKG"
        "${SUDO[@]}" rm -rf "/usr/lib/$OLD_PKG" "/usr/share/$OLD_PKG" || true
    fi
}

stage_files() {   # $1 = каталог; кладёт плоско всё, что перечислено в gui/PKGBUILD source=()
    local d="$1"
    cp "$HERE"/gui/{PKGBUILD,zapret_discord_gui.py,zapret-discord-gui,zapret-discord-helper} "$d/"
    cp "$HERE"/gui/{io.github.zapret_discord_gui.policy,io.github.zapret_discord_gui.desktop,zapret-discord-gui.svg} "$d/"
    cp "$HERE"/scripts/{zapret-install.sh,zapret-uninstall.sh,check.sh} "$d/"
    cp "$HERE"/conf/zapret2/{nfqws2-discord.conf,zapret-hosts-user.txt} "$d/"
    cp "$HERE/LICENSE" "$d/"
}

direct_install() {
    local lib=/usr/lib/$PKG share=/usr/share/$PKG S=("${SUDO[@]}")
    "${S[@]}" install -Dm644 "$HERE/gui/zapret_discord_gui.py" "$lib/zapret_discord_gui.py"
    "${S[@]}" install -Dm755 "$HERE/gui/zapret-discord-helper" "$lib/zapret-discord-helper"
    "${S[@]}" install -Dm755 "$HERE/gui/zapret-discord-gui" /usr/bin/zapret-discord-gui
    "${S[@]}" install -Dm644 "$HERE/gui/io.github.zapret_discord_gui.policy" /usr/share/polkit-1/actions/io.github.zapret_discord_gui.policy
    "${S[@]}" install -Dm644 "$HERE/gui/io.github.zapret_discord_gui.desktop" /usr/share/applications/io.github.zapret_discord_gui.desktop
    "${S[@]}" install -Dm644 "$HERE/gui/zapret-discord-gui.svg" /usr/share/icons/hicolor/scalable/apps/zapret-discord-gui.svg
    "${S[@]}" install -Dm755 "$HERE/scripts/zapret-install.sh"   "$share/scripts/zapret-install.sh"
    "${S[@]}" install -Dm755 "$HERE/scripts/zapret-uninstall.sh" "$share/scripts/zapret-uninstall.sh"
    "${S[@]}" install -Dm755 "$HERE/scripts/check.sh"            "$share/scripts/check.sh"
    "${S[@]}" install -Dm644 "$HERE/conf/zapret2/nfqws2-discord.conf"   "$share/conf/zapret2/nfqws2-discord.conf"
    "${S[@]}" install -Dm644 "$HERE/conf/zapret2/zapret-hosts-user.txt" "$share/conf/zapret2/zapret-hosts-user.txt"
    "${S[@]}" install -Dm644 "$HERE/LICENSE" "/usr/share/licenses/$PKG/LICENSE"
    "${S[@]}" gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
    "${S[@]}" update-desktop-database -q /usr/share/applications 2>/dev/null || true
}

install_gui() {
    msg "Шаг 2/2: GUI «Zapret Discord» (пакет $PKG)"
    remove_old_gui
    if (( DIRECT )); then
        msg "Ставлю зависимости GUI: ${GUI_DEPS[*]}"
        "${SUDO[@]}" pacman -S --needed --noconfirm "${GUI_DEPS[@]}"
        direct_install
    else
        msg "Ставлю зависимости сборки и GUI"
        "${SUDO[@]}" pacman -S --needed --noconfirm base-devel "${GUI_DEPS[@]}"
        BUILD_DIR="$(as_user mktemp -d -t zapret-discord-arch.XXXXXX)"
        trap 'rm -rf "$BUILD_DIR"' EXIT
        stage_files "$BUILD_DIR"
        [[ $EUID -eq 0 ]] && chown -R "$BUILD_USER": "$BUILD_DIR"
        local build="$BUILD_DIR"
        msg "makepkg в $build (от $BUILD_USER)"
        ( cd "$build" && as_user makepkg -f --noconfirm )
        "${SUDO[@]}" pacman -U --noconfirm "$build"/$PKG-*.pkg.tar.*
    fi
}

(( DO_ZAPRET )) && install_zapret
(( DO_GUI )) && install_gui

echo
msg "Готово."
(( DO_GUI )) && msg "Запуск: меню приложений → «Zapret Discord» или команда zapret-discord-gui"
(( DO_GUI )) && msg "Нужен polkit-агент (GNOME/KDE/XFCE — уже есть; i3/sway/Hyprland: sudo pacman -S polkit-gnome)"
(( DO_ZAPRET )) && msg "Перезапустите Discord полностью (из трея тоже) и проверьте: ./check.sh"
exit 0
