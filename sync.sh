#!/usr/bin/env bash
# sync.sh — CSAR file sync
#
#   cd ~/Downloads && unzip -o files.zip
#   source sync.sh | tee -a ~/claudette/sync.log
#   rm files.zip sync.sh

set -euo pipefail
CSAR_ROOT="$HOME/claudette/CSAR"
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok=0; fail=0; skip=0

check() {
  local f="$1" expected="$2"
  if [ ! -f "$f" ]; then echo -e "${YLW}SKIP${NC}  $f"; (( skip++ )) || true; return; fi
  local actual; actual=$(sha256sum "$f" | awk '{print $1}')
  if [ "$actual" = "$expected" ]; then echo -e "${GRN}OK${NC}    $f"; (( ok++ )) || true
  else echo -e "${RED}FAIL${NC}  $f"; echo "       expected: $expected";
       echo "       got:      $actual"; (( fail++ )) || true; fi
}

deploy() {
  local f="$1" dest="$2"
  [ ! -f "$f" ] && return
  dest="${dest/#~/$HOME}"; mkdir -p "$(dirname "$dest")"; cp "$f" "$dest"
}

echo "=== verify $(date '+%Y-%m-%dT%H:%M:%S') ==="
  check "vaise_seed.txt" "38f3cecd8263b45b0d973dae5d94bf9f440f63fc0e2ea5b410e177ec568a9c27"
  check "Renata.txt" "6c0fff81bc1a717cfff427e9132e71da9511cf3f23321903309eba296bc4eeb6"
  check "Hacker.txt" "5cb2f27890b773c674db3db821b8cb6591d4c758e3826ef4d8b1164ded91089c"
  check "Mail.txt" "4d60d1b3aecbe791b8910d475f5a3beb136b39b3b857afc0e9812c9d364c70ef"
  check "Witch.txt" "c052aa9d3db665f976fdebdab9fcbe33e495c8eda3f0a4785303c409815152f6"
  check "Flower.txt" "fb7c53d2d1c4c9513abb74c0c8bc9de82bdaba85bab69f63229ab813a17e258d"
  check "Vase001.txt" "85365fca3f89ebb2e358b38735648f588a4528234dc2fa0f691db2c34def415f"
  check "Voice.txt" "6bb0c24d8521acc211c32368bc64a719b28147beed8b184d8061ddb692c80503"

echo ""
if [ "$fail" -gt 0 ]; then echo -e "${RED}$fail failed — aborting.${NC}";
  return 1 2>/dev/null || exit 1; fi
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
echo ""
echo -e "${GRN}done.${NC} $ok deployed, $skip skipped."
echo "=== git ==="
cd "$CSAR_ROOT"
git status --short
echo "Run: git add -A && git commit -m \"your message\""
