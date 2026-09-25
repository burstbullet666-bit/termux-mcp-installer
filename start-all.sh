#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
#  start-all.sh — Master Launcher Termux MCP
#
#  Menjalankan semua komponen:
#    1. termux-native-mcp  → :8082 (MCP backend)
#    2. oauth_wrapper.py   → :8081 (OAuth + forward ke :8082)
#    3. cloudflared tunnel → Cloudflare
#
#  Cara pakai: MCP
# ============================================================

set -u

BASE="$HOME/termux-mcp"
LOG_DIR="$BASE/logs"
TMP_DIR="$BASE/tmp"

MCP_LOG="$LOG_DIR/mcp.log"
OAUTH_LOG="$LOG_DIR/oauth.log"
CF_LOG="$LOG_DIR/cloudflared.log"

MCP_PID_FILE="$BASE/mcp.pid"
OAUTH_PID_FILE="$BASE/oauth.pid"
CF_PID_FILE="$BASE/cloudflared.pid"

MCP_TOKEN_FILE="$HOME/.config/termux-mcp/token"
CF_TOKEN_FILE="$HOME/.config/cloudflared/tunnel-token"
PUBLIC_URL_FILE="$HOME/.config/termux-mcp/public-url"

OAUTH_WRAPPER="$BASE/oauth_wrapper.py"

MCP_PORT=8082
OAUTH_PORT=8081
PUBLIC_URL="$(cat "$PUBLIC_URL_FILE" 2>/dev/null || echo 'https://mcp.example.com')"

mkdir -p "$LOG_DIR" "$TMP_DIR"

# ─── Warna ──────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}[✓]${NC} $1"; }
fail() { echo -e "${RED}[✗]${NC} $1"; }
info() { echo -e "${CYAN}[→]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
sep()  { echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# ─── Header ─────────────────────────────────────────────────
clear
echo
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║         TERMUX MCP — MASTER LAUNCHER            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo
echo "  MCP backend   → :$MCP_PORT"
echo "  OAuth wrapper → :$OAUTH_PORT"
echo "  Public        → $PUBLIC_URL"
echo

# ─── Cek file wajib ─────────────────────────────────────────
sep
info "Memeriksa konfigurasi..."

[ ! -s "$MCP_TOKEN_FILE" ] && fail "MCP token tidak ditemukan: $MCP_TOKEN_FILE" && exit 1
ok "MCP token          : $(head -c12 "$MCP_TOKEN_FILE")..."

[ ! -s "$CF_TOKEN_FILE" ] && fail "Cloudflare token tidak ditemukan: $CF_TOKEN_FILE" && exit 1
ok "Cloudflare token   : OK"

[ ! -f "$OAUTH_WRAPPER" ] && fail "oauth_wrapper.py tidak ditemukan: $OAUTH_WRAPPER" && exit 1
ok "OAuth wrapper      : OK"

# ─── Stop semua proses lama ──────────────────────────────────
sep
info "Menghentikan proses lama..."

_stop_pid() {
    local PID_FILE="$1" LABEL="$2"
    if [ -f "$PID_FILE" ]; then
        local PID; PID="$(cat "$PID_FILE" 2>/dev/null || true)"
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
            echo "    Stopped $LABEL (PID $PID)"
        fi
        rm -f "$PID_FILE"
    fi
}

_stop_pid "$MCP_PID_FILE"   "MCP backend"
_stop_pid "$OAUTH_PID_FILE" "OAuth wrapper"
_stop_pid "$CF_PID_FILE"    "Cloudflared"

for PORT in $MCP_PORT $OAUTH_PORT; do
    for PID in $(lsof -t -iTCP:$PORT -sTCP:LISTEN 2>/dev/null || true); do
        kill "$PID" 2>/dev/null || true
        echo "    Freed port $PORT (PID $PID)"
    done
done

pkill -f "termux-native-mcp.*--port.*$MCP_PORT" 2>/dev/null || true
pkill -f "oauth_wrapper.py" 2>/dev/null || true
pkill -f "cloudflared tunnel run" 2>/dev/null || true
sleep 2
ok "Proses lama dihentikan"

# ─── Bersihkan log ───────────────────────────────────────────
: > "$MCP_LOG"; : > "$OAUTH_LOG"; : > "$CF_LOG"

# ─── 1. Start MCP backend :8082 ─────────────────────────────
sep
info "Menjalankan MCP backend di :$MCP_PORT ..."

nohup termux-native-mcp --host 127.0.0.1 --port "$MCP_PORT" >> "$MCP_LOG" 2>&1 &
MCP_PID=$!
echo "$MCP_PID" > "$MCP_PID_FILE"

MCP_READY=0
for i in $(seq 1 20); do
    CODE=$(curl -sf --max-time 2 -o /dev/null -w "%{http_code}" \
        "http://127.0.0.1:$MCP_PORT/mcp" 2>/dev/null || true)
    if [[ "$CODE" =~ ^(200|400|401|405)$ ]]; then MCP_READY=1; break; fi
    kill -0 "$MCP_PID" 2>/dev/null || { fail "MCP backend crash!"; tail -20 "$MCP_LOG"; exit 1; }
    sleep 1
done

[ "$MCP_READY" != "1" ] && fail "MCP backend timeout" && tail -20 "$MCP_LOG" && exit 1
ok "MCP backend        : HTTP $CODE (PID $MCP_PID)"

# ─── 2. Start OAuth wrapper :8081 ────────────────────────────
sep
info "Menjalankan OAuth wrapper di :$OAUTH_PORT ..."

nohup python3 "$OAUTH_WRAPPER" >> "$OAUTH_LOG" 2>&1 &
OAUTH_PID=$!
echo "$OAUTH_PID" > "$OAUTH_PID_FILE"

OAUTH_READY=0
for i in $(seq 1 15); do
    CODE=$(curl -sf --max-time 2 -o /dev/null -w "%{http_code}" \
        "http://127.0.0.1:$OAUTH_PORT/health" 2>/dev/null || true)
    [ "$CODE" = "200" ] && OAUTH_READY=1 && break
    kill -0 "$OAUTH_PID" 2>/dev/null || { fail "OAuth wrapper crash!"; tail -20 "$OAUTH_LOG"; exit 1; }
    sleep 1
done

[ "$OAUTH_READY" != "1" ] && fail "OAuth wrapper timeout" && tail -20 "$OAUTH_LOG" && exit 1
ok "OAuth wrapper      : HTTP 200 (PID $OAUTH_PID)"

DISC=$(curl -sf --max-time 3 \
    "http://127.0.0.1:$OAUTH_PORT/.well-known/oauth-authorization-server" 2>/dev/null || true)
echo "$DISC" | grep -q "authorization_endpoint" && ok "OAuth discovery    : OK" || warn "OAuth discovery belum siap"

# ─── 3. Start Cloudflare Tunnel ──────────────────────────────
sep
info "Menjalankan Cloudflare Tunnel ..."

nohup cloudflared tunnel run --token-file "$CF_TOKEN_FILE" >> "$CF_LOG" 2>&1 &
CF_PID=$!
echo "$CF_PID" > "$CF_PID_FILE"

CF_READY=0
for i in $(seq 1 45); do
    grep -q "Registered tunnel connection" "$CF_LOG" 2>/dev/null && CF_READY=1 && break
    kill -0 "$CF_PID" 2>/dev/null || { fail "Cloudflared crash!"; tail -20 "$CF_LOG"; exit 1; }
    sleep 1
done

[ "$CF_READY" != "1" ] && fail "Cloudflare tunnel timeout" && tail -20 "$CF_LOG" && exit 1
CONN_COUNT=$(grep -c "Registered tunnel connection" "$CF_LOG" 2>/dev/null || echo "0")
ok "Cloudflare tunnel  : $CONN_COUNT koneksi (PID $CF_PID)"

# ─── 4. Test public endpoint ─────────────────────────────────
sep
info "Menguji public endpoint..."
sleep 3

PUB_CODE=$(curl -sf --max-time 8 -o /dev/null -w "%{http_code}" \
    "$PUBLIC_URL/mcp" 2>/dev/null || true)
[[ "$PUB_CODE" =~ ^(200|401|405)$ ]] && \
    ok "Public HTTPS       : HTTP $PUB_CODE ✅" || \
    warn "Public HTTPS       : HTTP $PUB_CODE (tunnel mungkin masih propagating)"

PUB_DISC=$(curl -sf --max-time 8 \
    "$PUBLIC_URL/.well-known/oauth-authorization-server" 2>/dev/null || true)
echo "$PUB_DISC" | grep -q "authorization_endpoint" && \
    ok "Public OAuth       : OK ✅" || \
    warn "Public OAuth       : belum siap, tunggu 10 detik lagi"

# ─── Ringkasan ───────────────────────────────────────────────
sep
echo
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║               🚀  SEMUA BERJALAN  🚀            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo
echo -e "  ${GREEN}MCP backend${NC}   → http://127.0.0.1:$MCP_PORT  (PID $MCP_PID)"
echo -e "  ${GREEN}OAuth wrapper${NC} → http://127.0.0.1:$OAUTH_PORT  (PID $OAUTH_PID)"
echo -e "  ${GREEN}Cloudflare${NC}    → $PUBLIC_URL  (PID $CF_PID)"
echo
echo "  Tambahkan ke Claude.ai / ChatGPT:"
echo "  $PUBLIC_URL/mcp"
echo
echo "  Log: tail -f $LOG_DIR/*.log"
echo

# ─── Live log ────────────────────────────────────────────────
sep
echo -e "${YELLOW}  LIVE LOG — Ctrl+C untuk keluar (proses tetap jalan)${NC}"
sep
echo

trap 'echo; echo "Log ditutup. Proses tetap berjalan di background."; exit 0' INT

tail -F -n 0 "$MCP_LOG" "$OAUTH_LOG" "$CF_LOG"
