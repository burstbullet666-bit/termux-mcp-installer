# 🤖 Termux MCP Installer

Connect **Claude.ai**, **ChatGPT**, dan AI lainnya langsung ke Android kamu via MCP (Model Context Protocol).

```
Claude.ai / ChatGPT
       ↓ HTTPS + OAuth
  Cloudflare Tunnel
       ↓
  oauth_wrapper.py :8081
       ↓
  termux-native-mcp :8082
       ↓
     Android 🤖
```

---

## ⚡ Quick Start

```bash
git clone https://github.com/burstbullet666-bit/termux-mcp-installer
cd termux-mcp-installer
bash install.sh
```

Selesai! Lalu jalankan kapan saja dengan:

```bash
MCP
```

---

## 📋 Yang Kamu Butuhkan

Sebelum install, siapkan:

1. **Cloudflare Tunnel Token**
   - Buka [dash.cloudflare.com](https://dash.cloudflare.com)
   - Zero Trust → Networks → Tunnels → Create Tunnel
   - Copy token-nya

2. **Public URL** dari Cloudflare tunnel kamu
   - Contoh: `https://mcp.domain-kamu.com`
   - Pastikan ingress diarahkan ke `http://127.0.0.1:8081`

---

## 🔧 Cara Install

```bash
# 1. Clone repo
git clone https://github.com/burstbullet666-bit/termux-mcp-installer
cd termux-mcp-installer

# 2. Jalankan installer
bash install.sh

# 3. Ikuti instruksi di layar:
#    - Masukkan Cloudflare Tunnel Token
#    - Masukkan Public URL kamu

# 4. Jalankan!
MCP
```

---

## 🚀 Cara Pakai

Setelah install, cukup ketik:

```bash
MCP
```

Script otomatis:
- ✅ Stop proses lama
- ✅ Start `termux-native-mcp` di `:8082`
- ✅ Start `oauth_wrapper.py` di `:8081`
- ✅ Start `cloudflared` tunnel
- ✅ Test public endpoint
- ✅ Tampilkan live log

---

## 🔗 Menghubungkan ke AI

### Claude.ai
1. Settings → Integrations → Add MCP Server
2. URL: `https://url-kamu.com/mcp`
3. Klik Connect → selesai!

### ChatGPT
1. Settings → Connectors → Create MCP
2. URL: `https://url-kamu.com/mcp`
3. Authentication: OAuth (otomatis)

---

## 📁 Struktur File

```
termux-mcp-installer/
├── install.sh          # Installer utama
├── start-all.sh        # Master launcher (dipasang ke ~/termux-mcp/)
├── oauth_wrapper.py    # OAuth bridge (dipasang ke ~/termux-mcp/)
├── resources.txt       # Daftar dependencies
└── README.md           # Dokumentasi ini
```

Setelah install, file tersimpan di:
```
~/termux-mcp/
├── start-all.sh
├── oauth_wrapper.py
├── logs/
│   ├── mcp.log
│   ├── oauth.log
│   └── cloudflared.log
└── tmp/

~/.config/
├── termux-mcp/
│   ├── token          # MCP Bearer token (auto-generated)
│   └── public-url     # URL publik kamu
└── cloudflared/
    └── tunnel-token   # Cloudflare tunnel token
```

---

## 🛠️ Troubleshooting

**MCP tidak jalan:**
```bash
tail -f ~/termux-mcp/logs/mcp.log
```

**OAuth error:**
```bash
tail -f ~/termux-mcp/logs/oauth.log
```

**Cloudflare disconnect:**
```bash
tail -f ~/termux-mcp/logs/cloudflared.log
```

**Reset semua & mulai ulang:**
```bash
MCP
```

---

## ⚠️ Keamanan

- MCP Bearer token di-generate otomatis dan tersimpan di `~/.config/termux-mcp/token`
- Jangan share token ke orang lain
- Jika token bocor, hapus dan jalankan ulang installer

---

## 📜 License

MIT License — bebas digunakan dan dimodifikasi.

---

Made with ❤️ dari Android
