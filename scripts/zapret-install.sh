#!/usr/bin/env bash
# zapret-discord-arch: ядро установки обхода DPI (запускается от root).
# Обычно вызывается из ../install.sh или из GUI (helper через pkexec), но можно и напрямую.
#
# Режимы:
#   sudo scripts/zapret-install.sh            - вариант A (рекомендуемый): адаптер Sergeydigl3
#                                               (nfqws из bol-van/zapret v72.9 + стратегии Flowseal), nftables, systemd
#   sudo scripts/zapret-install.sh --zapret2  - вариант B: AUR-пакет zapret2 (bol-van/zapret2) + Discord-конфиг
#
# Опции варианта A:
#   --strategy NAME     стратегия Flowseal (по умолчанию general.bat; список: ./service.sh strategy list)
#   --iface NAME        сетевой интерфейс (по умолчанию any)
#   --no-service        не ставить systemd-сервис (только скачать и настроить)
set -euo pipefail

ADAPTER_REPO="https://github.com/Sergeydigl3/zapret-discord-youtube-linux.git"
ADAPTER_REV="${ADAPTER_REV:-69db771b527ba11ca71efe28728a7f9491eec352}"   # проверенный коммит (2026-08-17)
ADAPTER_DIR="/opt/zapret-discord-youtube-linux"
Z2_DIR="/opt/zapret2"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR="$(cd "$HERE/../conf/zapret2" 2>/dev/null && pwd || true)"   # repo и /usr/share/zapret-discord-arch

MODE="adapter"; STRATEGY="general.bat"; IFACE="any"; WITH_SERVICE=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --zapret2) MODE="zapret2"; shift ;;
        --strategy) STRATEGY="$2"; shift 2 ;;
        --iface) IFACE="$2"; shift 2 ;;
        --no-service) WITH_SERVICE=0; shift ;;
        -h|--help) sed -n 2,15p "$0"; exit 0 ;;
        *) echo "Неизвестный параметр: $1" >&2; exit 1 ;;
    esac
done

msg()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==> ВНИМАНИЕ:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m==> ОШИБКА:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запустите через sudo: sudo $0 $*"
command -v pacman >/dev/null || die "pacman не найден - скрипт рассчитан на Arch Linux (и производные: EndeavourOS, Manjaro, CachyOS)"

# Конфликт двух реализаций: адаптер при остановке делает 'pkill -f nfqws', что задевает и nfqws2
conflict_check() {
    if [[ $MODE == adapter ]] && systemctl is-enabled --quiet zapret2.service 2>/dev/null; then
        die "включён zapret2.service. Отключите: sudo systemctl disable --now zapret2"
    fi
    if [[ $MODE == zapret2 ]] && systemctl is-enabled --quiet zapret_discord_youtube.service 2>/dev/null; then
        die "включён zapret_discord_youtube.service. Удалите адаптер: sudo $HERE/zapret-uninstall.sh"
    fi
}

install_adapter() {
    msg "Ставлю зависимости из официальных репозиториев Arch"
    pacman -S --needed --noconfirm git curl nftables grep sed procps-ng

    if [[ -d $ADAPTER_DIR/.git ]]; then
        msg "Обновляю $ADAPTER_DIR"
        git -C "$ADAPTER_DIR" fetch --quiet origin
    else
        msg "Клонирую адаптер в $ADAPTER_DIR"
        git clone --quiet "$ADAPTER_REPO" "$ADAPTER_DIR"
    fi
    git -C "$ADAPTER_DIR" -c advice.detachedHead=false checkout --quiet "$ADAPTER_REV"

    cd "$ADAPTER_DIR"
    msg "Скачиваю nfqws (bol-van/zapret v72.9) и стратегии Flowseal (рекомендованные версии)"
    ./service.sh download-deps --default

    ./service.sh strategy list | grep -qx "$STRATEGY" || die "стратегия $STRATEGY не найдена. Список: $ADAPTER_DIR/service.sh strategy list"

    msg "Пишу $ADAPTER_DIR/conf.env (strategy=$STRATEGY, interface=$IFACE, firewall=nftables)"
    cat > conf.env <<CONF
strategy=$STRATEGY
interface=$IFACE
gamefiltertcp=false
gamefilterudp=false
firewall_backend=nftables
CONF

    if (( WITH_SERVICE )); then
        msg "Создаю и запускаю systemd-сервис zapret_discord_youtube"
        ./service.sh service install
        sleep 2
        systemctl --no-pager --lines=5 status zapret_discord_youtube.service || true
    else
        msg "Сервис не ставлю. Ручной запуск: sudo $ADAPTER_DIR/service.sh run --config $ADAPTER_DIR/conf.env"
    fi
}

install_zapret2() {
    local user="${SUDO_USER:-}"
    [[ -n $user && $user != root ]] || die "для сборки AUR-пакета нужен обычный пользователь: запустите через sudo из-под своей учётки"

    msg "Ставлю зависимости сборки и nftables"
    pacman -S --needed --noconfirm base-devel git nftables

    if pacman -Qq zapret2 >/dev/null 2>&1; then
        msg "Пакет zapret2 уже установлен: $(pacman -Q zapret2)"
    else
        local build; build="$(sudo -u "$user" mktemp -d)"
        msg "Собираю AUR zapret2 в $build (makepkg от $user)"
        sudo -u "$user" git clone --quiet https://aur.archlinux.org/zapret2.git "$build/zapret2"
        ( cd "$build/zapret2" && sudo -u "$user" makepkg -s --noconfirm --needed )
        pacman -U --noconfirm "$build"/zapret2/zapret2-*.pkg.tar.zst
        rm -rf "$build"
    fi
    [[ -f $Z2_DIR/config ]] || die "$Z2_DIR/config не найден после установки пакета"
    [[ -f $CONF_DIR/nfqws2-discord.conf ]] || die "не найден conf/zapret2/nfqws2-discord.conf рядом со скриптом"

    msg "Применяю Discord-конфиг к $Z2_DIR/config (бэкап: config.bak-zapret2-discord)"
    [[ -f $Z2_DIR/config.bak-zapret2-discord ]] || cp -a "$Z2_DIR/config" "$Z2_DIR/config.bak-zapret2-discord"
    sed -i '/^# --- zapret2-discord BEGIN ---$/,/^# --- zapret2-discord END ---$/d' "$Z2_DIR/config"
    sed -n '/^# --- zapret2-discord BEGIN ---$/,/^# --- zapret2-discord END ---$/p' \
        "$CONF_DIR/nfqws2-discord.conf" >> "$Z2_DIR/config"

    msg "Ставлю хостлист Discord: $Z2_DIR/ipset/zapret-hosts-user.txt"
    if [[ -f $Z2_DIR/ipset/zapret-hosts-user.txt ]]; then
        cat "$Z2_DIR/ipset/zapret-hosts-user.txt" "$CONF_DIR/zapret-hosts-user.txt" | sort -u > /tmp/zhu.$$
        install -m644 /tmp/zhu.$$ "$Z2_DIR/ipset/zapret-hosts-user.txt"; rm -f /tmp/zhu.$$
    else
        install -m644 "$CONF_DIR/zapret-hosts-user.txt" "$Z2_DIR/ipset/zapret-hosts-user.txt"
    fi

    if (( WITH_SERVICE )); then
        msg "Включаю и запускаю zapret2.service"
        systemctl daemon-reload
        systemctl enable --now zapret2.service
        systemctl restart zapret2.service
        sleep 2
        systemctl --no-pager --lines=5 status zapret2.service || true
    else
        msg "Сервис не включаю. Ручной запуск: sudo /opt/zapret2/init.d/sysv/zapret2 start"
    fi
}

conflict_check
case "$MODE" in
    adapter) install_adapter ;;
    zapret2) install_zapret2 ;;
esac
msg "Готово. Перезапустите Discord и проверьте: sudo $HERE/check.sh (или кнопка «Проверить» в GUI)"
