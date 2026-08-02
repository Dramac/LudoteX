#!/usr/bin/env bash
# =====================================================================
# Relevé des ressources du VPS pendant un test de charge.
#
# À lancer SUR LE SERVEUR, dans une seconde session SSH, juste avant de
# démarrer le générateur de charge depuis le poste de test :
#
#     ./mesures.sh 600 ludotex-formation /var/lib/ludotex-formation
#
# Produit un CSV horodaté (un échantillon toutes les 5 s) et, à la fin, un
# résumé + les erreurs remontées par le service pendant la fenêtre.
# Lecture seule : ce script ne touche à rien.
# =====================================================================
set -uo pipefail

DUREE="${1:-600}"                                   # secondes
SERVICE="${2:-ludotex-formation}"                   # unité systemd à suivre
DOSSIER_BASES="${3:-/var/lib/ludotex-formation}"    # où vivent les .db
SORTIE="mesures-$(date +%Y%m%d-%H%M%S).csv"
DEBUT_ISO="$(date --iso-8601=seconds)"

PID="$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || echo 0)"
if [ "${PID:-0}" = "0" ]; then
  echo "⚠ Service « $SERVICE » introuvable ou arrêté : les colonnes CPU/RAM"
  echo "  du processus resteront vides. Le reste (charge, disque) est valable."
fi

echo "Relevé de $DUREE s → $SORTIE  (Ctrl+C pour arrêter plus tôt)"
echo "horodatage,charge_1min,cpu_pct,rss_mo,mem_libre_mo,disque_libre_mo,wal_kio,connexions_443" > "$SORTIE"

FIN=$(( $(date +%s) + DUREE ))
MAX_RSS=0; MAX_WAL=0
while [ "$(date +%s)" -lt "$FIN" ]; do
  HORO="$(date +%H:%M:%S)"
  CHARGE="$(cut -d' ' -f1 /proc/loadavg)"
  if [ "${PID:-0}" != "0" ] && [ -d "/proc/$PID" ]; then
    LIGNE="$(ps -p "$PID" -o %cpu=,rss= 2>/dev/null)"
    CPU="$(echo "$LIGNE" | awk '{print $1}')"
    RSS="$(echo "$LIGNE" | awk '{printf "%.0f", $2/1024}')"
  else
    CPU=""; RSS=""
  fi
  MEM="$(awk '/MemAvailable/ {printf "%.0f", $2/1024}' /proc/meminfo)"
  DISQUE="$(df -m --output=avail "$DOSSIER_BASES" 2>/dev/null | tail -1 | tr -d ' ')"
  WAL="$(du -sk "$DOSSIER_BASES"/*.db-wal 2>/dev/null | awk '{s+=$1} END {print s+0}')"
  CONN="$(ss -Hant state established '( sport = :443 )' 2>/dev/null | wc -l)"

  echo "$HORO,$CHARGE,$CPU,$RSS,$MEM,$DISQUE,$WAL,$CONN" >> "$SORTIE"
  [ -n "$RSS" ] && [ "$RSS" -gt "$MAX_RSS" ] && MAX_RSS="$RSS"
  [ "$WAL" -gt "$MAX_WAL" ] && MAX_WAL="$WAL"
  sleep 5
done

echo
echo "================= RÉSUMÉ ================="
awk -F, 'NR>1 {
  n++;
  if ($2+0 > chmax) chmax = $2+0;
  if ($3 != "") { cpu += $3; ncpu++; if ($3+0 > cpumax) cpumax = $3+0 }
  if ($5+0 > 0 && (memmin == 0 || $5+0 < memmin)) memmin = $5+0;
  if ($8+0 > connmax) connmax = $8+0;
} END {
  printf "  échantillons        : %d\n", n;
  printf "  charge 1 min (max)  : %.2f\n", chmax;
  if (ncpu) printf "  CPU du service      : %.0f %% en moyenne, %.0f %% au pic\n", cpu/ncpu, cpumax;
  printf "  mémoire libre (min) : %d Mo\n", memmin;
  printf "  connexions 443 (max): %d\n", connmax;
}' "$SORTIE"
echo "  RSS du service (max): ${MAX_RSS} Mo"
echo "  fichiers WAL (max)  : ${MAX_WAL} Kio"
echo "  cœurs disponibles   : $(nproc)"
echo
echo "--- Erreurs du service pendant la fenêtre ---"
journalctl -u "$SERVICE" --since "$DEBUT_ISO" --no-pager 2>/dev/null \
  | grep -Ei "error|exception|traceback|locked|500 |timeout" | tail -40 \
  || echo "  (journal inaccessible — relancer avec sudo)"
echo
echo "CSV complet : $SORTIE"
