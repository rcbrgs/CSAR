#!/usr/bin/env bash
# sync.sh — CSAR file sync
#
# Usage (from ~/Downloads after unzip):
#   cd ~/Downloads && unzip -o files.zip
#   source sync.sh | tee -a ~/claudette/sync.log
#   rm files.zip sync.sh
# sync.sh cleans up the .txt files it deployed; files.zip and sync.sh
# are removed by the caller (see above).

set -euo pipefail
CSAR_ROOT="$HOME/claudette/CSAR"
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok=0; fail=0; skip=0

check() {
  local f="$1" expected="$2"
  if [ ! -f "$f" ]; then echo -e "${YLW}SKIP${NC}  $f"; (( skip++ )) || true; return; fi
  local actual; actual=$(sha256sum "$f" | awk '{print $1}')
  if [ "$actual" = "$expected" ]; then echo -e "${GRN}OK${NC}    $f"; (( ok++ )) || true
  else echo -e "${RED}FAIL${NC}  $f"
       echo "       expected: $expected"
       echo "       got:      $actual"
       (( fail++ )) || true; fi
}

deploy() {
  local f="$1" dest="$2"
  [ ! -f "$f" ] && return
  dest="${dest/#~/$HOME}"; mkdir -p "$(dirname "$dest")"; cp "$f" "$dest"
}

echo "=== verify $(date '+%Y-%m-%dT%H:%M:%S') ==="
# sync.sh excluded — always freshly generated
  check "vaise_seed.txt" "38f3cecd8263b45b0d973dae5d94bf9f440f63fc0e2ea5b410e177ec568a9c27"
  check "Renata.txt" "c4038ecc600f5851a11fd809126ad622361dfad9656a9f750deb5c21b3e37626"
  check "Hacker.txt" "c014feda61fb942a09307840d908c6f60dfb14f6e04c9ed98f3c6759f81a7c5e"
  check "Mail.txt" "0e70b76a124dd5d672bbc26ff515cd2a6781b3d09040736e885ced922c5df09c"
  check "Witch.txt" "c052aa9d3db665f976fdebdab9fcbe33e495c8eda3f0a4785303c409815152f6"
  check "Flower.txt" "fb7c53d2d1c4c9513abb74c0c8bc9de82bdaba85bab69f63229ab813a17e258d"
  check "Vase001.txt" "85365fca3f89ebb2e358b38735648f588a4528234dc2fa0f691db2c34def415f"
  check "Voice.txt" "6bb0c24d8521acc211c32368bc64a719b28147beed8b184d8061ddb692c80503"

echo ""
if [ "$fail" -gt 0 ]; then
  echo -e "${RED}$fail failed — aborting deploy.${NC}"
  return 1 2>/dev/null || exit 1
fi

echo "=== deploy ==="
  deploy "vaise_seed.txt" "~/claudette/CSAR/Private/vaise_seed.txt"
  deploy "Renata.txt" "~/claudette/CSAR/Private/Renata.txt"
  deploy "Hacker.txt" "~/claudette/CSAR/Private/Hacker.txt"
  deploy "Mail.txt" "~/claudette/CSAR/Mail.txt"
  deploy "Witch.txt" "~/claudette/CSAR/Private/Witch.txt"
  deploy "Flower.txt" "~/claudette/CSAR/Private/Flower.txt"
  deploy "Vase001.txt" "~/claudette/CSAR/Private/Vase001.txt"
  deploy "Voice.txt" "~/claudette/CSAR/Voice.txt"
  deploy "sync.sh" "~/claudette/CSAR/sync.sh"

echo "=== cleanup ==="
for f in "vaise_seed.txt" "Renata.txt" "Hacker.txt" "Mail.txt" "Witch.txt" "Flower.txt" "Vase001.txt" "Voice.txt"; do
  [ -f "$f" ] && rm "$f" && echo "  rm  $f" || true
done

echo ""
echo -e "${GRN}done.${NC} $ok deployed, $skip skipped."
echo "=== git ==="
cd "$CSAR_ROOT"
git status --short
echo "Run: git add -A && git commit -m \"your message\""
