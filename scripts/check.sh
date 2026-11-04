#!/usr/bin/env bash
# Диагностика zapret на Arch: сервис, процессы, правила nftables, доступность Discord.
# Запуск: ./check.sh (из корня репо) или sudo scripts/check.sh
set -u
ok()   { printf '  \033[1;32m[OK]\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m[!!]\033[0m %s\n' "$*"; }
info() { printf '\n\033[1m%s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || echo "(без sudo часть проверок nftables не покажется)"

info "1. Сервисы"
for s in zapret_discord_youtube zapret2; do
    if systemctl list-unit-files "$s.service" --no-legend 2>/dev/null | grep -q "$s"; then
        st="$(systemctl is-active "$s.service" 2>/dev/null)"
        [[ $st == active ]] && ok "$s.service: active" || bad "$s.service: $st (journalctl -u $s -b)"
    fi
done

info "2. Процессы nfqws/nfqws2"
if pgrep -a 'nfqws' >/dev/null; then
    pgrep -a 'nfqws' | cut -c1-200 | sed 's/^/  /'; ok "nfqws работает"
else
    bad "nfqws не запущен"
fi
if pgrep -x nfqws >/dev/null && pgrep -x nfqws2 >/dev/null; then
    bad "одновременно запущены nfqws и nfqws2 - оставьте один вариант"
fi

info "3. nftables"
if command -v nft >/dev/null; then
    for t in "inet zapretunix" "inet zapret2"; do
        if nft list table $t >/dev/null 2>&1; then
            ok "таблица '$t' есть, правил с queue: $(nft list table $t | grep -c 'queue')"
        fi
    done
    nft list tables 2>/dev/null | grep -qE 'zapretunix|zapret2' || bad "таблиц zapret в nftables нет (нужен root, либо сервис не запущен)"
else
    bad "nft не установлен: sudo pacman -S nftables"
fi
lsmod 2>/dev/null | grep -q '^nfnetlink_queue' && ok "модуль nfnetlink_queue загружен" || echo "  (nfnetlink_queue не виден в lsmod - ядро подгрузит его при первом queue)"

info "4. Доступность Discord (HTTPS, таймаут 10с)"
for u in https://discord.com/api/v9/gateway https://gateway.discord.gg https://cdn.discordapp.com https://discord.media; do
    code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$u" 2>/dev/null)"
    if [[ $code =~ ^[1-4][0-9][0-9]$ ]]; then ok "$u -> HTTP $code (TLS прошёл)"; else bad "$u -> нет ответа/обрыв ($code)"; fi
done
echo
echo "Голос (UDP 19294-19344, 50000-50100) curl'ом не проверить: зайдите в голосовой канал;"
echo "зависание на 'RTC connecting' = нужна другая стратегия (см. README, раздел 'Подбор стратегии')."
