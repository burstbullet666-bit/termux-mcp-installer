#!/data/data/com.termux/files/usr/bin/bash
# stop-all.sh — Hentikan semua komponen MCP
# Dipanggil manual atau otomatis

BASE="$HOME/termux-mcp"

echo "[MCP] Menghentikan semua proses..."

for PID_FILE in "$BASE/mcp.pid" "$BASE/oauth.pid" "$BASE/cloudflared.pid" "$BASE/mt.pid"; do
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" && echo "  Stopped PID $PID ($(basename $PID_FILE .pid))"
        fi
        rm -f "$PID_FILE"
    fi
done

pkill -f "termux-native-mcp" 2>/dev/null || true
pkill -f "oauth_wrapper.py" 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
pkill -f "cf.py" 2>/dev/null || true

echo "[MCP] ✅ Semua proses dihentikan"
