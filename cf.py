import os
import sys
import time
import signal
import shutil
import subprocess
import threading
import queue
from datetime import datetime


# ============================================================
#                MODDING TOOL BY GENDEV
#           MT MCP HTTPS TUNNEL - NAMED TUNNEL
# ============================================================
#
# MODE:
#   Cloudflare Named Tunnel
#
# TUNNEL:
#   mt-mcp
#
# DOMAIN:
#   https://mcp.sinchannexter.my.id
#
# MCP:
#   https://mcp.sinchannexter.my.id/mcp
#
# LOCAL:
#   http://127.0.0.1:8787
#
# CATATAN:
#   Sebelum menjalankan script ini, pastikan:
#
#   1. cloudflared sudah login
#   2. tunnel "mt-mcp" sudah dibuat
#   3. ~/.cloudflared/config.yml sudah benar
#   4. DNS mcp.sinchannexter.my.id sudah diroute ke tunnel
#
# Script TIDAK lagi menggunakan Quick Tunnel.
# Domain tidak akan berubah saat restart.
# ============================================================


# ============================================================
#                         CONFIG
# ============================================================

TUNNEL_NAME = "mt-mcp"

TARGET_URL = "http://127.0.0.1:9876"

PUBLIC_HOSTNAME = "mcp.sinchannexter.my.id"
PUBLIC_URL = f"https://{PUBLIC_HOSTNAME}"
MCP_URL = f"{PUBLIC_URL}/mcp"

CONFIG_FILE = os.path.expanduser(
    "~/.cloudflared/config.yml"
)

CLOUDFLARED_COMMAND = [
    "cloudflared",
    "--config",
    CONFIG_FILE,
    "tunnel",
    "run",
    TUNNEL_NAME,
]


# ============================================================
#                         GLOBAL
# ============================================================

process = None
reader_thread = None
input_thread = None

running = True

started_at = None
last_connection = None

connection_count = 0
error_count = 0

tunnel_registered = False

log_queue = queue.Queue()
command_queue = queue.Queue()

state_lock = threading.Lock()

recent_logs = []

cleanup_done = False


# ============================================================
#                          ANSI
# ============================================================

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

FG = {
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
    "white": "\033[97m",
}

SPINNER = [
    "⠋", "⠙", "⠹", "⠸", "⠼",
    "⠴", "⠦", "⠧", "⠇", "⠏"
]

PULSE = [
    "▁", "▂", "▃", "▄", "▅",
    "▆", "▇", "█", "▇", "▆",
    "▅", "▄", "▃", "▂"
]


# ============================================================
#                         UTILITY
# ============================================================

def color(text, name):
    return FG.get(name, "") + str(text) + RESET


def clear_screen():
    print("\033[2J\033[H", end="", flush=True)


def hide_cursor():
    print("\033[?25l", end="", flush=True)


def show_cursor():
    print("\033[?25h", end="", flush=True)


def terminal_width():
    return shutil.get_terminal_size((80, 24)).columns


def fit(text, width=None):
    if width is None:
        width = terminal_width()

    text = str(text)

    if width <= 3:
        return text[:width]

    if len(text) <= width:
        return text

    return text[:width - 3] + "..."


def format_uptime():
    if not started_at:
        return "00:00:00"

    elapsed = int(time.time() - started_at)

    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def now():
    return datetime.now().strftime("%H:%M:%S")


def clear_line():
    print("\033[2K\r", end="", flush=True)


def move_cursor_up(lines):
    if lines > 0:
        print(
            f"\033[{lines}A",
            end="",
            flush=True
        )


# ============================================================
#                    TERMINAL INPUT
# ============================================================

class RawInput:
    def __init__(self):
        self.fd = None
        self.old_settings = None
        self.available = False

        try:
            import termios
            import tty

            if sys.stdin.isatty():
                self.fd = sys.stdin.fileno()

                self.old_settings = (
                    termios.tcgetattr(self.fd)
                )

                tty.setcbreak(self.fd)

                self.available = True

        except Exception:
            self.available = False

    def read_char(self):
        if not self.available:
            return None

        try:
            import select

            ready, _, _ = select.select(
                [self.fd],
                [],
                [],
                0
            )

            if ready:
                data = os.read(
                    self.fd,
                    1
                )

                if data:
                    return data.decode(
                        errors="ignore"
                    )

        except Exception:
            return None

        return None

    def restore(self):
        if self.available and self.old_settings is not None:

            try:
                import termios

                termios.tcsetattr(
                    self.fd,
                    termios.TCSADRAIN,
                    self.old_settings
                )

            except Exception:
                pass


raw_input = None


# ============================================================
#                     PROCESS HANDLING
# ============================================================

def reader_worker(proc):
    global connection_count
    global error_count
    global last_connection
    global tunnel_registered

    try:

        while running:

            line = proc.stdout.readline()

            if not line:
                break

            line = line.rstrip("\r\n")

            if not line:
                continue

            # Simpan log tanpa menampilkan ke terminal.
            log_queue.put(
                ("log", line)
            )

            # ------------------------------------------------
            # REGISTERED
            # ------------------------------------------------

            if "Registered tunnel connection" in line:

                with state_lock:

                    connection_count += 1

                    last_connection = time.time()

                    tunnel_registered = True

                log_queue.put(
                    (
                        "registered",
                        line
                    )
                )

            # ------------------------------------------------
            # CONNECTION CLOSED
            # ------------------------------------------------

            elif "connection closed" in line.lower():

                with state_lock:
                    error_count += 1

                log_queue.put(
                    (
                        "connection_closed",
                        line
                    )
                )

            # ------------------------------------------------
            # ERROR
            # ------------------------------------------------

            elif (
                "Serve tunnel error" in line
                or "failed" in line.lower()
                or "error" in line.lower()
            ):

                with state_lock:
                    error_count += 1

                log_queue.put(
                    (
                        "error",
                        line
                    )
                )

            # ------------------------------------------------
            # OFFLINE
            # ------------------------------------------------

            elif (
                "no more connections active"
                in line.lower()
            ):

                with state_lock:
                    tunnel_registered = False

                log_queue.put(
                    (
                        "offline",
                        line
                    )
                )

    except Exception as exc:

        log_queue.put(
            (
                "reader_error",
                str(exc)
            )
        )


def cloudflared_exists():
    return shutil.which(
        "cloudflared"
    ) is not None


def config_exists():
    return os.path.isfile(
        CONFIG_FILE
    )


def start_cloudflared():
    global process
    global reader_thread
    global started_at
    global tunnel_registered

    if not cloudflared_exists():

        log_queue.put(
            (
                "fatal",
                "cloudflared tidak ditemukan di PATH."
            )
        )

        return False

    if not config_exists():

        log_queue.put(
            (
                "fatal",
                f"config.yml tidak ditemukan: {CONFIG_FILE}"
            )
        )

        return False

    try:

        with state_lock:
            tunnel_registered = False

        process = subprocess.Popen(
            CLOUDFLARED_COMMAND,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        started_at = time.time()

        reader_thread = threading.Thread(
            target=reader_worker,
            args=(process,),
            daemon=True,
        )

        reader_thread.start()

        return True

    except FileNotFoundError:

        log_queue.put(
            (
                "fatal",
                "cloudflared tidak ditemukan."
            )
        )

        return False

    except Exception as exc:

        log_queue.put(
            (
                "fatal",
                f"Gagal menjalankan cloudflared: {exc}"
            )
        )

        return False


def stop_cloudflared():
    global process

    proc = process

    if proc is None:
        return

    if proc.poll() is None:

        try:

            proc.terminate()

            proc.wait(
                timeout=5
            )

        except subprocess.TimeoutExpired:

            try:

                proc.kill()

                proc.wait(
                    timeout=2
                )

            except Exception:
                pass

        except Exception:

            try:
                proc.kill()
            except Exception:
                pass

    process = None


def restart_cloudflared():
    global started_at
    global tunnel_registered

    log_queue.put(
        (
            "system",
            "Restarting Named Tunnel..."
        )
    )

    stop_cloudflared()

    with state_lock:

        tunnel_registered = False

    started_at = None

    time.sleep(0.8)

    if start_cloudflared():

        log_queue.put(
            (
                "success",
                "Named Tunnel mt-mcp berhasil dimulai ulang."
            )
        )

    else:

        log_queue.put(
            (
                "fatal",
                "Restart gagal."
            )
        )


# ============================================================
#                         UI HEADER
# ============================================================

def print_banner():

    clear_screen()

    width = max(
        60,
        terminal_width()
    )

    logo = [
        "███╗   ███╗ ██████╗ ██████╗ ██╗███╗   ██╗ ██████╗ ",
        "████╗ ████║██╔════╝ ██╔══██╗██║████╗  ██║██╔════╝ ",
        "██╔████╔██║██║  ███╗██████╔╝██║██╔██╗ ██║██║  ███╗",
        "██║╚██╔╝██║██║   ██║██╔═══╝ ██║██║╚██╗██║██║   ██║",
        "██║ ╚═╝ ██║╚██████╔╝██║     ██║██║ ╚████║╚██████╔╝",
        "╚═╝     ╚═╝ ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═══╝ ╚═════╝ ",
    ]

    print()

    print(
        color(
            "═" * width,
            "cyan"
        )
    )

    for line in logo:

        padding = max(
            0,
            (width - len(line)) // 2
        )

        print(
            " " * padding
            + color(
                line,
                "cyan"
            )
        )

    print()

    title = "MODDING TOOL BY GENDEV"
    subtitle = "MT MCP HTTPS NAMED TUNNEL"

    print(
        " " * max(
            0,
            (width - len(title)) // 2
        )
        + BOLD
        + color(
            title,
            "magenta"
        )
        + RESET
    )

    print(
        " " * max(
            0,
            (width - len(subtitle)) // 2
        )
        + color(
            subtitle,
            "white"
        )
    )

    print(
        color(
            "═" * width,
            "cyan"
        )
    )

    print()


# ============================================================
#                         WELCOME
# ============================================================

def print_welcome():

    print()

    print(
        color(
            "╭─ ",
            "cyan"
        )
        + BOLD
        + "GENDEV COMMAND CENTER"
        + RESET
        + color(
            " ─────────────────────────╮",
            "cyan"
        )
    )

    print(
        color("│ ", "cyan")
        + "MT MCP Named Tunnel siap digunakan."
    )

    print(
        color("│ ", "cyan")
        + "Domain permanen:"
        + " "
        + color(
            PUBLIC_HOSTNAME,
            "green"
        )
    )

    print(
        color("│ ", "cyan")
        + "Dashboard diperbarui di tempat yang sama."
    )

    print(
        color(
            "╰────────────────────────────────────────────────────╯",
            "cyan"
        )
    )

    print()


# ============================================================
#                         STATUS
# ============================================================

def current_status():

    global process

    if process is None:
        return "STOPPED", "red"

    if process.poll() is not None:
        return "OFFLINE", "red"

    with state_lock:

        registered = tunnel_registered

    if registered:
        return "ONLINE", "green"

    return "CONNECTING", "yellow"


def render_status(
    spinner_index=0,
    pulse_index=0
):

    width = max(
        60,
        terminal_width()
    )

    status, status_color = (
        current_status()
    )

    with state_lock:

        connections = connection_count

        errors = error_count

        registered = tunnel_registered

    spinner = SPINNER[
        spinner_index % len(SPINNER)
    ]

    pulse = PULSE[
        pulse_index % len(PULSE)
    ]

    # ========================================================
    # HEADER STATUS
    # ========================================================

    clear_line()

    print(
        color(
            f"{spinner} ",
            "cyan"
        )
        + BOLD
        + "TUNNEL STATUS"
        + RESET
        + "  "
        + color(
            status,
            status_color
        )
        + "   "
        + color(
            f"Uptime {format_uptime()}",
            "white"
        )
        + "   "
        + color(
            f"Conn {connections}",
            "green"
            if connections
            else "yellow"
        )
        + "   "
        + color(
            f"Err {errors}",
            "red"
            if errors
            else "green"
        )
    )

    # ========================================================
    # HTTPS
    # ========================================================

    clear_line()

    print(
        color(
            "◆ ",
            "green"
            if registered
            else "yellow"
        )
        + BOLD
        + "HTTPS"
        + RESET
        + " : "
        + color(
            PUBLIC_URL,
            "cyan"
            if registered
            else "yellow"
        )
    )

    # ========================================================
    # MCP
    # ========================================================

    clear_line()

    print(
        color(
            "◆ ",
            "green"
            if registered
            else "yellow"
        )
        + BOLD
        + "MCP"
        + RESET
        + "    : "
        + color(
            MCP_URL,
            "cyan"
            if registered
            else "yellow"
        )
    )

    # ========================================================
    # TARGET
    # ========================================================

    clear_line()

    print(
        color(
            "◆ ",
            "blue"
        )
        + "TARGET"
        + "  : "
        + color(
            TARGET_URL,
            "white"
        )
    )

    # ========================================================
    # TUNNEL
    # ========================================================

    clear_line()

    print(
        color(
            "◆ ",
            "blue"
        )
        + "TUNNEL"
        + "   : "
        + color(
            TUNNEL_NAME,
            "magenta"
        )
        + "  "
        + color(
            "(Named Tunnel)",
            "white"
        )
    )

    # ========================================================
    # CONFIG
    # ========================================================

    clear_line()

    print(
        color(
            "◆ ",
            "blue"
        )
        + "CONFIG"
        + "   : "
        + color(
            CONFIG_FILE,
            "white"
        )
    )

    # ========================================================
    # MENU
    # ========================================================

    clear_line()

    print(
        color("[R]", "cyan")
        + " Restart   "
        + color("[S]", "green")
        + " Status   "
        + color("[L]", "yellow")
        + " Logs   "
        + color("[C]", "magenta")
        + " Clear   "
        + color("[Q]", "red")
        + " Quit"
    )

    # ========================================================
    # SEPARATOR
    # ========================================================

    clear_line()

    print(
        color(
            "─" * width,
            "blue"
        )
    )


# ============================================================
#                       LOG MANAGEMENT
# ============================================================

def add_log(line):

    if not line:
        return

    # Hindari duplicate beruntun.
    if (
        recent_logs
        and recent_logs[-1] == line
    ):
        return

    recent_logs.append(line)

    if len(recent_logs) > 15:
        del recent_logs[:-15]


def process_events():

    changed = False

    while True:

        try:

            event_type, data = (
                log_queue.get_nowait()
            )

        except queue.Empty:

            break

        changed = True

        if event_type == "log":

            add_log(data)

        elif event_type == "registered":

            add_log(
                f"[{now()}] [✓] "
                "Tunnel connection REGISTERED"
            )

        elif event_type == "connection_closed":

            add_log(
                f"[{now()}] [!] "
                "Tunnel connection closed"
            )

        elif event_type == "error":

            add_log(
                f"[{now()}] [!] "
                "Tunnel error detected"
            )

        elif event_type == "offline":

            add_log(
                f"[{now()}] [!] "
                "Semua koneksi tunnel mati"
            )

        elif event_type == "reader_error":

            add_log(
                f"[{now()}] [!] "
                f"Reader error: {data}"
            )

        elif event_type == "fatal":

            add_log(
                f"[{now()}] [FATAL] {data}"
            )

        elif event_type == "system":

            add_log(
                f"[{now()}] [SYSTEM] {data}"
            )

        elif event_type == "success":

            add_log(
                f"[{now()}] [✓] {data}"
            )

    return changed


# ============================================================
#                       LOG VIEW
# ============================================================

def render_logs():

    clear_screen()

    width = max(
        60,
        terminal_width()
    )

    print(
        color(
            "╭"
            + "─" * (width - 2)
            + "╮",
            "cyan"
        )
    )

    title = " CLOUDFLARED LOG VIEW "

    print(
        color(
            "│"
            + title.center(width - 2)
            + "│",
            "cyan"
        )
    )

    print(
        color(
            "├"
            + "─" * (width - 2)
            + "┤",
            "cyan"
        )
    )

    if not recent_logs:

        print(
            color("│ ", "blue")
            + "Belum ada log.".ljust(
                width - 3
            )
            + color(
                "│",
                "blue"
            )
        )

    else:

        for line in recent_logs:

            clean = fit(
                line,
                width - 4
            )

            print(
                color(
                    "│ ",
                    "blue"
                )
                + clean
                + " " * max(
                    0,
                    width - len(clean) - 3
                )
                + color(
                    "│",
                    "blue"
                )
            )

    print(
        color(
            "╰"
            + "─" * (width - 2)
            + "╯",
            "cyan"
        )
    )

    print()

    print(
        color("[B]", "cyan")
        + " Kembali"
        + "    "
        + color("[Q]", "red")
        + " Quit"
    )


# ============================================================
#                     FULL STATUS VIEW
# ============================================================

def show_full_status():

    clear_screen()

    width = max(
        60,
        terminal_width()
    )

    with state_lock:

        connections = connection_count

        errors = error_count

        registered = tunnel_registered

    status, status_color = (
        current_status()
    )

    print()

    print(
        color(
            "╔"
            + "═" * (width - 2)
            + "╗",
            "cyan"
        )
    )

    title = " TUNNEL INFORMATION "

    print(
        color(
            "║"
            + title.center(width - 2)
            + "║",
            "cyan"
        )
    )

    print(
        color(
            "╠"
            + "═" * (width - 2)
            + "╣",
            "cyan"
        )
    )

    rows = [
        (
            "Status",
            color(
                status,
                status_color
            )
        ),
        (
            "Tunnel",
            TUNNEL_NAME
        ),
        (
            "Hostname",
            PUBLIC_HOSTNAME
        ),
        (
            "HTTPS",
            PUBLIC_URL
        ),
        (
            "MCP",
            MCP_URL
        ),
        (
            "Target",
            TARGET_URL
        ),
        (
            "Protocol",
            "HTTP/2"
        ),
        (
            "Config",
            CONFIG_FILE
        ),
        (
            "Registered",
            "YES"
            if registered
            else "NO"
        ),
        (
            "Connections",
            str(connections)
        ),
        (
            "Errors",
            str(errors)
        ),
        (
            "Uptime",
            format_uptime()
        ),
    ]

    for key, value in rows:

        content = (
            f"  {key:<12} : {value}"
        )

        print(
            color(
                "║",
                "cyan"
            )
            + fit(
                content,
                width - 3
            ).ljust(
                width - 2
            )
            + color(
                "║",
                "cyan"
            )
        )

    print(
        color(
            "╚"
            + "═" * (width - 2)
            + "╝",
            "cyan"
        )
    )

    print()

    print(
        color("[B]", "cyan")
        + " Back"
        + "    "
        + color("[Q]", "red")
        + " Quit"
    )


# ============================================================
#                    COMMAND PROCESSOR
# ============================================================

def get_command(timeout=0.1):

    try:

        return command_queue.get(
            timeout=timeout
        )

    except queue.Empty:

        return None


def handle_dashboard_command(command):

    if not command:
        return False

    command = command.lower()

    # --------------------------------------------------------
    # RESTART
    # --------------------------------------------------------

    if command == "r":

        threading.Thread(
            target=restart_cloudflared,
            daemon=True
        ).start()

        return True

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if command == "s":

        show_full_status()

        while running:

            process_events()

            key = get_command(
                timeout=0.1
            )

            if not key:
                continue

            key = key.lower()

            if key == "b":

                clear_screen()

                print_banner()

                print_welcome()

                return True

            if key == "q":

                cleanup()

                return False

        return False

    # --------------------------------------------------------
    # LOGS
    # --------------------------------------------------------

    if command == "l":

        render_logs()

        while running:

            process_events()

            key = get_command(
                timeout=0.1
            )

            if not key:
                continue

            key = key.lower()

            if key == "b":

                clear_screen()

                print_banner()

                print_welcome()

                return True

            if key == "q":

                cleanup()

                return False

        return False

    # --------------------------------------------------------
    # CLEAR
    # --------------------------------------------------------

    if command == "c":

        recent_logs.clear()

        log_queue.put(
            (
                "system",
                "Logs cleared."
            )
        )

        return True

    # --------------------------------------------------------
    # QUIT
    # --------------------------------------------------------

    if command == "q":

        cleanup()

        return False

    return True


# ============================================================
#                     INPUT WORKER
# ============================================================

def input_worker():

    global running

    while running:

        char = None

        if raw_input is not None:

            char = raw_input.read_char()

        if char:

            char = char.lower()

            if char in (
                "r",
                "s",
                "l",
                "c",
                "q",
                "b"
            ):

                command_queue.put(
                    char
                )

        time.sleep(0.05)


# ============================================================
#                 DASHBOARD RENDER ENGINE
# ============================================================

DASHBOARD_LINES = 8


def dashboard_loop():

    spinner_index = 0
    pulse_index = 0

    render_status(
        spinner_index,
        pulse_index
    )

    while running:

        process_events()

        command = get_command(
            timeout=0.12
        )

        if command:

            result = (
                handle_dashboard_command(
                    command
                )
            )

            if not running:
                break

            if not result:
                break

            # Setelah kembali dari view lain,
            # gambar dashboard penuh sekali.
            clear_screen()

            print_banner()

            print_welcome()

            for _ in range(
                DASHBOARD_LINES
            ):
                print()

        spinner_index += 1
        pulse_index += 1

        move_cursor_up(
            DASHBOARD_LINES
        )

        render_status(
            spinner_index,
            pulse_index
        )


# ============================================================
#                         CLEANUP
# ============================================================

def cleanup(signum=None, frame=None):

    global running
    global cleanup_done

    if cleanup_done:
        return

    cleanup_done = True

    running = False

    try:

        if raw_input:
            raw_input.restore()

    except Exception:
        pass

    show_cursor()

    print()
    print()

    print(
        color(
            "╭────────────────────────────────────────────╮",
            "yellow"
        )
    )

    text_line = (
        "│ Menghentikan cloudflared...               │"
    )

    print(
        color(
            text_line,
            "yellow"
        )
    )

    print(
        color(
            "╰────────────────────────────────────────────╯",
            "yellow"
        )
    )

    stop_cloudflared()

    time.sleep(0.3)

    print()

    print(
        BOLD
        + color(
            "✓ MT MCP Named Tunnel dihentikan.",
            "green"
        )
        + RESET
    )

    print(
        color(
            f"  Tunnel : {TUNNEL_NAME}",
            "magenta"
        )
    )

    print(
        color(
            f"  MCP    : {MCP_URL}",
            "cyan"
        )
    )

    print()


# ============================================================
#                     PRE-FLIGHT CHECK
# ============================================================

def preflight():

    problems = []

    if not cloudflared_exists():

        problems.append(
            "cloudflared tidak ditemukan di PATH."
        )

    if not config_exists():

        problems.append(
            f"config.yml tidak ditemukan: {CONFIG_FILE}"
        )

    if not problems:
        return True

    clear_screen()

    print()

    print(
        color(
            "╭────────────────────────────────────────────╮",
            "red"
        )
    )

    print(
        color(
            "│             PREFLIGHT ERROR                │",
            "red"
        )
    )

    print(
        color(
            "├────────────────────────────────────────────┤",
            "red"
        )
    )

    for problem in problems:

        text_line = fit(
            problem,
            40
        )

        print(
            color("│ ", "red")
            + text_line.ljust(40)
            + color("│", "red")
        )

    print(
        color(
            "╰────────────────────────────────────────────╯",
            "red"
        )
    )

    print()

    return False


# ============================================================
#                           MAIN
# ============================================================

def main():

    global raw_input
    global input_thread

    signal.signal(
        signal.SIGINT,
        cleanup
    )

    signal.signal(
        signal.SIGTERM,
        cleanup
    )

    raw_input = RawInput()

    hide_cursor()

    try:

        # ----------------------------------------------------
        # PREFLIGHT
        # ----------------------------------------------------

        if not preflight():

            time.sleep(2)

            cleanup()

            return

        # ----------------------------------------------------
        # UI
        # ----------------------------------------------------

        print_banner()

        print_welcome()

        print(
            color("[*] ", "cyan")
            + "Menjalankan "
            + BOLD
            + "Cloudflare Named Tunnel"
            + RESET
            + "..."
        )

        print(
            color("[*] ", "cyan")
            + "Tunnel   : "
            + color(
                TUNNEL_NAME,
                "magenta"
            )
        )

        print(
            color("[*] ", "cyan")
            + "Hostname : "
            + color(
                PUBLIC_HOSTNAME,
                "cyan"
            )
        )

        print(
            color("[*] ", "cyan")
            + "MCP      : "
            + color(
                MCP_URL,
                "green"
            )
        )

        print(
            color("[*] ", "cyan")
            + "Target   : "
            + color(
                TARGET_URL,
                "white"
            )
        )

        print(
            color("[*] ", "cyan")
            + "Protocol : "
            + color(
                "HTTP/2",
                "white"
            )
        )

        print()

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        if not start_cloudflared():

            time.sleep(1)

            cleanup()

            return

        print(
            color("[✓] ", "green")
            + "Process Cloudflare aktif."
        )

        print(
            color("[✓] ", "green")
            + "Menunggu koneksi tunnel..."
        )

        print()

        # ----------------------------------------------------
        # Input thread
        # ----------------------------------------------------

        if raw_input.available:

            input_thread = threading.Thread(
                target=input_worker,
                daemon=True
            )

            input_thread.start()

        # ----------------------------------------------------
        # Ruang dashboard
        # ----------------------------------------------------

        for _ in range(
            DASHBOARD_LINES
        ):
            print()

        # ----------------------------------------------------
        # Dashboard
        # ----------------------------------------------------

        dashboard_loop()

    except KeyboardInterrupt:

        cleanup()

    except Exception as exc:

        show_cursor()

        print()

        print(
            color(
                "[FATAL ERROR] ",
                "red"
            )
            + str(exc)
        )

        cleanup()


# ============================================================
#                          ENTRY
# ============================================================

if __name__ == "__main__":
    main()