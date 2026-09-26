#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
#  TERMUX MCP INSTALLER
#  github.com/burstbullet666-bit/termux-mcp-installer
#
#  Usage:
#    bash install.sh
#
#  Apa yang dilakukan:
#    1. Install dependencies (cloudflared, python3, curl, jq)
#    2. Download resources dari resources.txt
#    3. Setup token MCP dan Cloudflare
#    4. Install oauth_wrapper.py
#    5. Install start-all.sh
#    6. Daftarkan alias MCP di PATH
# ============================================================

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/termux-mcp"
CONFIG_DIR="$HOME/.config"
MCP_CONFIG="$CONFIG_DIR/termux-mcp"
CF_CONFIG="$CONFIG_DIR/cloudflared"
LOG_DIR="$INSTALL_DIR/logs"
TMP_DIR="$INSTALL_DIR/tmp"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[✓]${NC} $1"; }
fail() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
info() { echo -e "${CYAN}[→]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
ask()  { echo -e "${BOLD}[?]${NC} $1"; }
sep()  { echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# ─── Header ─────────────────────────────────────────────────

clear
echo
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║         TERMUX MCP INSTALLER v1.0               ║${NC}"
echo -e "${BOLD}║   Connect Claude & ChatGPT to your Android      ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo
echo "  Installer ini akan menyiapkan:"
echo "  • termux-native-mcp  (MCP backend)"
echo "  • oauth_wrapper.py   (OAuth bridge)"
echo "  • cloudflared        (Tunnel ke internet)"
echo "  • Alias MCP          (satu perintah untuk semua)"
echo
read -p "  Lanjutkan? [Y/n]: " CONFIRM
[[ "$CONFIRM" =~ ^[Nn] ]] && echo "Dibatalkan." && exit 0

# ─── 1. Buat direktori ───────────────────────────────────────

sep
info "Membuat direktori..."
mkdir -p "$INSTALL_DIR" "$LOG_DIR" "$TMP_DIR" "$MCP_CONFIG" "$CF_CONFIG"
ok "Direktori siap"

# ─── 2. Update package index ─────────────────────────────────

sep
info "Update package index Termux..."
pkg update -y -q 2>/dev/null || warn "Update gagal, lanjut..."
ok "Package index diperbarui"

# ─── 3. Install dependencies dari resources.txt ──────────────

sep
info "Menginstall dependencies..."

RESOURCES_FILE="$REPO_DIR/resources.txt"
if [ ! -f "$RESOURCES_FILE" ]; then
    fail "resources.txt tidak ditemukan di $REPO_DIR"
fi

while IFS= read -r line || [ -n "$line" ]; do
    # Skip komentar dan baris kosong
    [[ "$line" =~ ^#.*$ ]] && continue
    [[ -z "$line" ]] && continue

    TYPE=$(echo "$line" | cut -d'|' -f1)
    NAME=$(echo "$line" | cut -d'|' -f2)
    URL=$(echo "$line" | cut -d'|' -f3)

    case "$TYPE" in
        pkg)
            if ! command -v "$NAME" &>/dev/null; then
                info "  Installing $NAME..."
                pkg install -y "$NAME" -q 2>/dev/null && ok "  $NAME installed" || warn "  $NAME gagal (lanjut)"
            else
                ok "  $NAME sudah ada"
            fi
            ;;
        pip)
            info "  pip install $NAME..."
            pip install "$NAME" -q --break-system-packages 2>/dev/null && ok "  $NAME installed" || warn "  $NAME gagal (lanjut)"
            ;;
        deb)
            info "  Download & install $NAME dari $URL..."
            DEB_FILE="$TMP_DIR/$NAME.deb"
            curl -fsSL "$URL" -o "$DEB_FILE" 2>/dev/null || { warn "  Gagal download $NAME"; continue; }
            dpkg -i "$DEB_FILE" 2>/dev/null && ok "  $NAME installed" || warn "  $NAME gagal install"
            ;;
        binary)
            info "  Download binary $NAME dari $URL..."
            curl -fsSL "$URL" -o "$PREFIX/bin/$NAME" 2>/dev/null || { warn "  Gagal download $NAME"; continue; }
            chmod +x "$PREFIX/bin/$NAME"
            ok "  $NAME installed"
            ;;
    esac
done < "$RESOURCES_FILE"

# ─── 4. Install termux-native-mcp ────────────────────────────

sep
info "Menginstall termux-native-mcp..."

if command -v termux-native-mcp &>/dev/null; then
    ok "termux-native-mcp sudah terinstall"
else
    # Install dari .deb yang disertakan di repo
    DEB_FILE="$REPO_DIR/termux-mcp_0.11.5_all.deb"
    if [ -f "$DEB_FILE" ]; then
        info "  Install dari paket lokal..."
        dpkg -i "$DEB_FILE" 2>/dev/null && \
            ok "termux-native-mcp installed via local deb" || \
            warn "dpkg gagal, coba pip..."
    fi

    # Fallback pip jika dpkg gagal
    if ! command -v termux-native-mcp &>/dev/null; then
        info "  Mencoba via pip..."
        pip install termux-native-mcp -q --break-system-packages 2>/dev/null && \
            ok "termux-native-mcp installed via pip" || \
            fail "termux-native-mcp gagal install! Coba manual: dpkg -i termux-mcp_0.11.5_all.deb"
    fi
fi

# ─── 5. Input konfigurasi user ───────────────────────────────

sep
echo -e "${BOLD}  KONFIGURASI${NC}"
echo
echo "  Kamu perlu menyiapkan:"
echo "  • Cloudflare Tunnel Token"
echo "  • Public URL MCP kamu"
echo

# Cloudflare Token
if [ -s "$CF_CONFIG/tunnel-token" ]; then
    warn "Cloudflare token sudah ada, skip input"
    CF_TOKEN=$(cat "$CF_CONFIG/tunnel-token")
else
    ask "Masukkan Cloudflare Tunnel Token:"
    read -r CF_TOKEN
    if [ -z "$CF_TOKEN" ]; then
        fail "Cloudflare token tidak boleh kosong!"
    fi
    echo "$CF_TOKEN" > "$CF_CONFIG/tunnel-token"
    chmod 600 "$CF_CONFIG/tunnel-token"
    ok "Cloudflare token disimpan"
fi

# Public URL
ask "Masukkan public URL MCP kamu (contoh: https://mcp.domain.com):"
read -r PUBLIC_URL
PUBLIC_URL="${PUBLIC_URL:-https://mcp.example.com}"
echo "$PUBLIC_URL" > "$MCP_CONFIG/public-url"
ok "Public URL disimpan: $PUBLIC_URL"

# MCP Token — generate otomatis
if [ -s "$MCP_CONFIG/token" ]; then
    warn "MCP token sudah ada, skip generate"
else
    MCP_TOKEN=$(python3 -c "import secrets; print('knrdt_' + secrets.token_urlsafe(32))")
    echo "$MCP_TOKEN" > "$MCP_CONFIG/token"
    chmod 600 "$MCP_CONFIG/token"
    ok "MCP token di-generate: ${MCP_TOKEN:0:12}..."
fi

# ─── 6. Install oauth_wrapper.py ─────────────────────────────

sep
info "Menginstall oauth_wrapper.py..."

# Patch PUBLIC_BASE_URL sesuai input user
sed "s|PUBLIC_BASE_URL = \".*\"|PUBLIC_BASE_URL = \"$PUBLIC_URL\"|g" \
    "$REPO_DIR/oauth_wrapper.py" > "$INSTALL_DIR/oauth_wrapper.py"

chmod +x "$INSTALL_DIR/oauth_wrapper.py"
ok "oauth_wrapper.py terinstall dan di-patch"

# ─── 7. Install start-all.sh ─────────────────────────────────

sep
info "Menginstall start-all.sh..."

cp "$REPO_DIR/start-all.sh" "$INSTALL_DIR/start-all.sh"
chmod +x "$INSTALL_DIR/start-all.sh"
ok "start-all.sh terinstall"

# ─── 8. Daftarkan alias MCP di PATH ──────────────────────────

sep
info "Mendaftarkan perintah MCP..."

MCP_BIN="$PREFIX/bin/MCP"
cat > "$MCP_BIN" << 'MCPEOF'
#!/data/data/com.termux/files/usr/bin/bash
bash ~/termux-mcp/start-all.sh
MCPEOF
chmod +x "$MCP_BIN"
ok "Perintah 'MCP' terdaftar di PATH"

# ─── 9. Selesai ──────────────────────────────────────────────

sep
echo
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║           ✅  INSTALASI SELESAI!                ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo
echo "  Untuk menjalankan semua komponen:"
echo
echo -e "  ${BOLD}MCP${NC}"
echo
echo "  Yang akan berjalan:"
echo "  • termux-native-mcp  → :8082"
echo "  • oauth_wrapper.py   → :8081"
echo "  • cloudflared tunnel → internet"
echo
echo "  Public MCP URL:"
echo "  $PUBLIC_URL/mcp"
echo
echo "  Tambahkan ke Claude.ai / ChatGPT sebagai MCP connector!"
echo
MCP_TOKEN=$(cat "$MCP_CONFIG/token" 2>/dev/null || echo "?")
echo "  MCP Bearer Token (simpan ini!):"
echo "  $MCP_TOKEN"
echo
