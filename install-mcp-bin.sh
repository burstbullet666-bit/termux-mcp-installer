#!/data/data/com.termux/files/usr/bin/bash
# Fix installer termux-native-mcp untuk Termux
# dpkg tidak bisa install langsung karena path berbeda

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEB_FILE="$SCRIPT_DIR/termux-mcp_0.11.5_all.deb"
TMP="$SCRIPT_DIR/tmp/deb-extract"
PREFIX="/data/data/com.termux/files/usr"

echo "[→] Menginstall termux-native-mcp..."
mkdir -p "$TMP" && cd "$TMP"

# Extract .deb
ar x "$DEB_FILE"
mkdir -p data-extracted
tar xf data.tar.xz -C data-extracted

# Copy binary ke Termux PREFIX
echo "[→] Copy binary..."
cp -f data-extracted/usr/bin/termux-mcp "$PREFIX/bin/termux-mcp" 2>/dev/null || true
cp -f data-extracted/usr/bin/termux-native-mcp "$PREFIX/bin/termux-native-mcp" 2>/dev/null || true
chmod +x "$PREFIX/bin/termux-mcp" "$PREFIX/bin/termux-native-mcp" 2>/dev/null || true

# Copy library Python
echo "[→] Copy library..."
LIB_SRC="data-extracted/usr/lib/termux-mcp"
LIB_DST="$PREFIX/lib/termux-mcp"
[ -d "$LIB_SRC" ] && mkdir -p "$LIB_DST" && cp -rf "$LIB_SRC/." "$LIB_DST/"

# Cleanup
cd ~ && rm -rf "$TMP"

# Verifikasi
if command -v termux-native-mcp &>/dev/null; then
    echo "[✓] termux-native-mcp berhasil terinstall!"
else
    echo "[✗] Gagal install!"
fi
