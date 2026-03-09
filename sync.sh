#!/usr/bin/env bash
# sync.sh — CSAR file sync
# NOTE: vaise.py → ~/claudette/ (outside CSAR repo — commit separately)
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
  check "vaise.py" "c1f0170eb7be65b7fd3345de841be60790a7f9e2c28f09fd8fde2d6bc057c5b1"
  check "vaise_seed.txt" "33b23e607613ddafa4182761ddb4e5aed91eb21bfab5890f8118da7ca4e58991"
  check "Renata.txt" "3e95733911abd8aa80826c62ada1d796903cdb193f028fea3f01ee05010599a4"
  check "Hacker.txt" "0c595c7eb85c3fdd8429eaaf6d69b958f95ddaee970873256313ff21fdfd4076"
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
  deploy "vaise.py" "~/claudette/vaise.py"
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
for f in "vaise.py" "vaise_seed.txt" "Renata.txt" "Hacker.txt" "Mail.txt" "Witch.txt" "Flower.txt" "Vase001.txt" "Voice.txt"; do
  [ -f "$f" ] && rm "$f" && echo "  rm  $f" || true
done

echo ""
echo -e "${GRN}done.${NC} $ok deployed, $skip skipped."
echo "=== git (CSAR) ==="
cd "$CSAR_ROOT"
git status --short
echo "Run: git add -A && git commit -m \"your message\""
echo ""
echo "NOTE: vaise.py → ~/claudette/ (separate repo — commit manually)"
