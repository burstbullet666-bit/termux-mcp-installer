#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
#  start-all.sh — Master Launcher Termux MCP
#
#  Menjalankan semua komponen sekaligus:
#    1. termux-native-mcp  → :8082  (MCP backend)
#    2. oauth_wrapper.py   → :8081  (OAuth + forward ke :8082)
#    3. cloudflared tunnel → Cloudflare (forward ke :8081)
#
#  Setelah semua siap, menampilkan live log ketiga komponen.
#
#  Cara pakai:
#    bash ~/termux-mcp/start-all.sh
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

OAUTH_WRAPPER="$BASE/oauth_wrapper.py"
CF_MT_SCRIPT="$BASE/cf.py"
MT_PID_FILE="$BASE/mt.pid"
MT_LOG="$LOG_DIR/mt.log"
MT_PORT=9876
MT_PUBLIC_URL="https://mt.dirgantarasiapmaba.my.id"

MCP_PORT=8082
OAUTH_PORT=8081
PUBLIC_URL="https://mcp.dirgantarasiapmaba.my.id"

mkdir -p "$LOG_DIR" "$TMP_DIR"

# ─── Warna ──────────────────────────────────────────────────

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

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
echo "  MCP backend  → :$MCP_PORT"
echo "  OAuth wrapper → :$OAUTH_PORT"
echo "  Public       → $PUBLIC_URL"
echo

# ─── Cek file wajib ─────────────────────────────────────────

sep
info "Memeriksa token..."

if [ ! -s "$MCP_TOKEN_FILE" ]; then
    fail "MCP token tidak ditemukan: $MCP_TOKEN_FILE"
    exit 1
fi
ok "MCP token          : $(head -c8 "$MCP_TOKEN_FILE")..."

if [ ! -s "$CF_TOKEN_FILE" ]; then
    fail "Cloudflare token tidak ditemukan: $CF_TOKEN_FILE"
    exit 1
fi
ok "Cloudflare token   : OK"

if [ ! -f "$OAUTH_WRAPPER" ]; then
    fail "oauth_wrapper.py tidak ditemukan: $OAUTH_WRAPPER"
    exit 1
fi
ok "OAuth wrapper      : $OAUTH_WRAPPER"

# ─── Stop semua proses lama ──────────────────────────────────

sep
info "Menghentikan proses lama..."

_stop_pid() {
    local PID_FILE="$1"
    local LABEL="$2"
    if [ -f "$PID_FILE" ]; then
        local PID
        PID="$(cat "$PID_FILE" 2>/dev/null || true)"
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
            echo "    Stopped $LABEL PID $PID"
        fi
        rm -f "$PID_FILE"
    fi
}

_stop_pid "$MCP_PID_FILE"   "MCP backend"
_stop_pid "$OAUTH_PID_FILE" "OAuth wrapper"
_stop_pid "$CF_PID_FILE"    "Cloudflared"

# Kill siapa saja yang masih pakai port
for PORT in $MCP_PORT $OAUTH_PORT; do
    for PID in $(lsof -t -iTCP:$PORT -sTCP:LISTEN 2>/dev/null || true); do
        kill "$PID" 2>/dev/null || true
        echo "    Freed port $PORT (PID $PID)"
    done
done

# Kill proses lama by name (fallback)
pkill -f "termux-native-mcp.*--port.*$MCP_PORT" 2>/dev/null || true
pkill -f "oauth_wrapper.py" 2>/dev/null || true
pkill -f "cloudflared tunnel run" 2>/dev/null || true

sleep 2
ok "Proses lama dihentikan"

# ─── Bersihkan log ───────────────────────────────────────────

: > "$MCP_LOG"
: > "$OAUTH_LOG"
: > "$CF_LOG"

# ─── 1. Start MCP backend :8082 ─────────────────────────────

sep
info "Menjalankan MCP backend di :$MCP_PORT ..."

nohup termux-native-mcp \
    --host 127.0.0.1 \
    --port "$MCP_PORT" \
    >> "$MCP_LOG" 2>&1 &

MCP_PID=$!
echo "$MCP_PID" > "$MCP_PID_FILE"

# Tunggu MCP siap
MCP_READY=0
for i in $(seq 1 20); do
    CODE=$(curl -sf --max-time 2 -o /dev/null -w "%{http_code}" \
        "http://127.0.0.1:$MCP_PORT/mcp" 2>/dev/null || true)
    if [ "$CODE" = "401" ] || [ "$CODE" = "200" ] || [ "$CODE" = "405" ] || [ "$CODE" = "400" ]; then
        MCP_READY=1
        break
    fi
    if ! kill -0 "$MCP_PID" 2>/dev/null; then
        fail "MCP backend crash!"
        echo
        tail -30 "$MCP_LOG"
        exit 1
    fi
    sleep 1
done

if [ "$MCP_READY" != "1" ]; then
    fail "MCP backend tidak merespons setelah 20 detik"
    echo
    tail -30 "$MCP_LOG"
    exit 1
fi

ok "MCP backend        : HTTP $CODE (PID $MCP_PID)"

# ─── 2. Start OAuth wrapper :8081 ────────────────────────────

sep
info "Menjalankan OAuth wrapper di :$OAUTH_PORT ..."

nohup python3 "$OAUTH_WRAPPER" \
    >> "$OAUTH_LOG" 2>&1 &

OAUTH_PID=$!
echo "$OAUTH_PID" > "$OAUTH_PID_FILE"

# Tunggu OAuth wrapper siap
OAUTH_READY=0
for i in $(seq 1 15); do
    CODE=$(curl -sf --max-time 2 -o /dev/null -w "%{http_code}" \
        "http://127.0.0.1:$OAUTH_PORT/health" 2>/dev/null || true)
    if [ "$CODE" = "200" ]; then
        OAUTH_READY=1
        break
    fi
    if ! kill -0 "$OAUTH_PID" 2>/dev/null; then
        fail "OAuth wrapper crash!"
        echo
        tail -30 "$OAUTH_LOG"
        exit 1
    fi
    sleep 1
done

if [ "$OAUTH_READY" != "1" ]; then
    fail "OAuth wrapper tidak merespons setelah 15 detik"
    echo
    tail -30 "$OAUTH_LOG"
    exit 1
fi

ok "OAuth wrapper      : HTTP 200 (PID $OAUTH_PID)"

# Verifikasi OAuth discovery
DISCOVERY=$(curl -sf --max-time 3 \
    "http://127.0.0.1:$OAUTH_PORT/.well-known/oauth-authorization-server" \
    2>/dev/null || true)

if echo "$DISCOVERY" | grep -q "authorization_endpoint"; then
    ok "OAuth discovery    : OK"
else
    warn "OAuth discovery tidak merespons dengan benar"
fi

# ─── 3. Start Cloudflare Tunnel ──────────────────────────────

sep
info "Menjalankan Cloudflare Tunnel ..."

nohup cloudflared --config "$HOME/.cloudflared/config.yml" tunnel run \
    >> "$CF_LOG" 2>&1 &

CF_PID=$!
echo "$CF_PID" > "$CF_PID_FILE"

# Tunggu tunnel registered
CF_READY=0
for i in $(seq 1 45); do
    if grep -q "Registered tunnel connection" "$CF_LOG" 2>/dev/null; then
        CF_READY=1
        break
    fi
    if ! kill -0 "$CF_PID" 2>/dev/null; then
        fail "Cloudflared crash!"
        echo
        tail -30 "$CF_LOG"
        exit 1
    fi
    sleep 1
done

if [ "$CF_READY" != "1" ]; then
    fail "Cloudflare tunnel belum registered setelah 45 detik"
    echo
    tail -30 "$CF_LOG"
    exit 1
fi

# Hitung jumlah koneksi
CONN_COUNT=$(grep -c "Registered tunnel connection" "$CF_LOG" 2>/dev/null || echo "0")
ok "Cloudflare tunnel  : $CONN_COUNT koneksi (PID $CF_PID)"

# ─── 4. Test public endpoint ─────────────────────────────────

sep
info "Menguji public endpoint ..."

sleep 3

PUB_CODE=$(curl -sf --max-time 8 -o /dev/null -w "%{http_code}" \
    "$PUBLIC_URL/mcp" 2>/dev/null || true)

if [ "$PUB_CODE" = "401" ] || [ "$PUB_CODE" = "200" ]; then
    ok "Public HTTPS       : HTTP $PUB_CODE ✅"
else
    warn "Public endpoint belum merespons (HTTP $PUB_CODE) — tunnel mungkin masih propagating"
fi

# Test public OAuth discovery
PUB_DISC=$(curl -sf --max-time 8 \
    "$PUBLIC_URL/.well-known/oauth-authorization-server" \
    2>/dev/null || true)

if echo "$PUB_DISC" | grep -q "authorization_endpoint"; then
    ok "Public OAuth discovery : OK ✅"
else
    warn "Public OAuth discovery belum tersedia — coba lagi dalam 10 detik"
fi

# ─── Ringkasan ───────────────────────────────────────────────

sep
echo
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║               🚀  SEMUA BERJALAN  🚀            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo
echo -e "  ${GREEN}MCP backend${NC}   → http://127.0.0.1:$MCP_PORT/mcp    (PID $MCP_PID)"
echo -e "  ${GREEN}OAuth wrapper${NC} → http://127.0.0.1:$OAUTH_PORT/mcp   (PID $OAUTH_PID)"
echo -e "  ${GREEN}Cloudflare${NC}    → $PUBLIC_URL  (PID $CF_PID)"
echo
echo "  OAuth discovery:"
echo "  $PUBLIC_URL/.well-known/oauth-authorization-server"
echo
echo "  Log files:"
echo "  MCP    → $MCP_LOG"
echo "  OAuth  → $OAUTH_LOG"
echo "  CF     → $CF_LOG"
echo

# ─── Live log ────────────────────────────────────────────────

sep
echo -e "${YELLOW}  LIVE LOG — Ctrl+C untuk keluar dari tampilan log${NC}"
echo -e "${YELLOW}  (Semua proses tetap berjalan di background)${NC}"
sep
echo

trap 'echo; echo "Log ditutup. Proses tetap berjalan."; exit 0' INT

tail -F -n 20 \
    --pid $CF_PID \
    "$MCP_LOG" \
    "$OAUTH_LOG" \
    "$CF_LOG"
