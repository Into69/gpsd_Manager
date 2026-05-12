"""GPSD Manager - FastAPI web application for managing a local gpsd instance."""

import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


# ---------------------------------------------------------------------------
# Core gpsd management
# ---------------------------------------------------------------------------

@dataclass
class GpsdStatus:
    installed: bool = False
    running: bool = False
    pid: int | None = None
    version: str | None = None
    devices: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    options: dict = field(default_factory=dict)
    has_permissions: bool = False
    gpsd_path: str | None = None


class GpsdManager:
    """Manages a local gpsd instance."""

    GPSD_DEFAULT_OPTIONS = {
        "-n": {"description": "Don't wait for a client to connect before polling GPS", "enabled": False},
        "-N": {"description": "Run in foreground (don't daemonize)", "enabled": False},
        "-G": {"description": "Listen on all interfaces, not just localhost", "enabled": False},
        "-b": {"description": "Read-only mode (no device writes)", "enabled": False},
    }

    GPSD_CONF_PATH = "/etc/default/gpsd"
    GPSD_SYSTEMD_SERVICE = "gpsd"

    def __init__(self):
        self.errors: list[str] = []
        self.options: dict = {k: dict(v) for k, v in self.GPSD_DEFAULT_OPTIONS.items()}
        self._load_config()

    def _run(self, cmd: list[str], check: bool = False, timeout: int = 10) -> subprocess.CompletedProcess:
        """Run a shell command and return the result."""
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=check,
            )
        except subprocess.TimeoutExpired:
            self.errors.append(f"Command timed out: {' '.join(cmd)}")
            return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="Timeout")
        except subprocess.CalledProcessError as e:
            self.errors.append(f"Command failed: {' '.join(cmd)}: {e.stderr}")
            return subprocess.CompletedProcess(cmd, returncode=e.returncode, stdout=e.stdout, stderr=e.stderr)
        except FileNotFoundError:
            self.errors.append(f"Command not found: {cmd[0]}")
            return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr=f"{cmd[0]} not found")

    def _expand_flags(self, args: list[str]) -> set[str]:
        """Expand combined flags like '-Gn' into individual flags {'-G', '-n'}."""
        expanded = set()
        for arg in args:
            if arg.startswith("-") and len(arg) > 2 and not arg.startswith("--"):
                for char in arg[1:]:
                    expanded.add(f"-{char}")
            else:
                expanded.add(arg)
        return expanded

    def _apply_flags(self, flags: set[str]):
        """Reset all options then enable only those present in flags."""
        for flag in self.options:
            self.options[flag]["enabled"] = flag in flags

    def _load_config(self):
        """Load saved options from /etc/default/gpsd if it exists."""
        conf = Path(self.GPSD_CONF_PATH)
        if not conf.exists():
            return
        try:
            content = conf.read_text()
            match = re.search(r'GPSD_OPTIONS="([^"]*)"', content)
            if match:
                flags = self._expand_flags(match.group(1).split())
                self._apply_flags(flags)
        except OSError:
            pass

    def sync_options_from_running(self):
        """Read the running gpsd process's command line and sync options to match."""
        # Try to get PID from systemd
        result = self._run(["systemctl", "show", self.GPSD_SYSTEMD_SERVICE, "--property=MainPID"])
        if result.returncode != 0:
            return
        match = re.search(r"MainPID=(\d+)", result.stdout)
        if not match or match.group(1) == "0":
            return

        pid = match.group(1)
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if not cmdline_path.exists():
            return

        try:
            raw = cmdline_path.read_bytes()
            args = raw.decode("utf-8", errors="replace").split("\x00")
        except OSError:
            return

        self._apply_flags(self._expand_flags(args))

    def get_configured_devices(self) -> list[str]:
        """Return the device paths currently set in /etc/default/gpsd."""
        conf = Path(self.GPSD_CONF_PATH)
        if not conf.exists():
            return []
        try:
            content = conf.read_text()
            match = re.search(r'DEVICES="([^"]*)"', content)
            if match and match.group(1).strip():
                return match.group(1).strip().split()
        except OSError:
            pass
        return []

    def check_installed(self) -> tuple[bool, str | None, str | None]:
        """Check if gpsd is installed and return (installed, version, path)."""
        gpsd_path = shutil.which("gpsd")
        if not gpsd_path:
            return False, None, None

        result = self._run(["gpsd", "-V"])
        version = None
        if result.returncode == 0:
            match = re.search(r"(\d+\.\d+)", result.stdout)
            if match:
                version = match.group(1)
        return True, version, gpsd_path

    def check_permissions(self) -> tuple[bool, list[str]]:
        """Check if we have permissions to manage gpsd."""
        issues = []

        uid = os.getuid()
        if uid != 0:
            result = self._run(["sudo", "-n", "systemctl", "status", self.GPSD_SYSTEMD_SERVICE])
            if result.returncode != 0 and "password" in result.stderr.lower():
                issues.append("Not running as root and passwordless sudo not available for systemctl")

        return len(issues) == 0, issues

    def get_status(self) -> GpsdStatus:
        """Get the full status of gpsd."""
        status = GpsdStatus()
        self.errors.clear()

        status.installed, status.version, status.gpsd_path = self.check_installed()
        if not status.installed:
            self.errors.append("gpsd is not installed. Install with: sudo apt install gpsd gpsd-clients")
            status.errors = list(self.errors)
            return status

        status.has_permissions, perm_issues = self.check_permissions()
        if perm_issues:
            self.errors.extend(perm_issues)

        result = self._run(["systemctl", "is-active", self.GPSD_SYSTEMD_SERVICE])
        status.running = result.returncode == 0 and result.stdout.strip() == "active"

        if status.running:
            result = self._run(["systemctl", "show", self.GPSD_SYSTEMD_SERVICE, "--property=MainPID"])
            if result.returncode == 0:
                match = re.search(r"MainPID=(\d+)", result.stdout)
                if match and match.group(1) != "0":
                    status.pid = int(match.group(1))

        status.devices = self._get_active_devices()
        status.options = self.options
        status.errors = list(self.errors)
        return status

    def _get_active_devices(self) -> list[str]:
        """Query gpsd for currently active devices."""
        result = self._run(["gpspipe", "-w", "-n", "5"], timeout=5)
        devices = set()
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                try:
                    data = json.loads(line)
                    if data.get("class") == "DEVICES":
                        for dev in data.get("devices", []):
                            path = dev.get("path")
                            if path:
                                devices.add(path)
                    elif data.get("class") == "DEVICE":
                        path = data.get("path")
                        if path:
                            devices.add(path)
                except json.JSONDecodeError:
                    continue
        return sorted(devices)

    def discover_devices(self) -> list[dict]:
        """Discover available serial/USB GPS devices on the system."""
        devices = []
        serial_paths = ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyS*", "/dev/ttyAMA*", "/dev/gps*", "/dev/pps*"]

        for pattern in serial_paths:
            for path in sorted(glob.glob(pattern)):
                info = {"path": path, "type": "serial", "description": ""}
                result = self._run(["udevadm", "info", "--name", path, "--query=property"])
                if result.returncode == 0:
                    props = {}
                    for line in result.stdout.splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            props[k] = v
                    vendor = props.get("ID_VENDOR", "")
                    model = props.get("ID_MODEL", "")
                    if vendor or model:
                        info["description"] = f"{vendor} {model}".strip()
                devices.append(info)

        return devices

    # See gpsd JSON protocol docs (TPV.status field)
    _STATUS_MAP = {
        1: "Normal", 2: "DGPS", 3: "RTK Fixed", 4: "RTK Float",
        5: "DR", 6: "GNSS+DR", 7: "Time only", 8: "Simulated", 9: "P(Y)",
    }

    # gpsd gnssid -> human name (NMEA 0183 v4.10 / u-blox)
    _GNSS_NAMES = {
        0: "GPS", 1: "SBAS", 2: "Galileo", 3: "BeiDou",
        4: "IMES", 5: "QZSS", 6: "GLONASS", 7: "NavIC",
    }

    @classmethod
    def _parse_tpv(cls, data: dict, result: dict):
        mode = data.get("mode", 0)
        result["fix"] = {0: "Unknown", 1: "No fix", 2: "2D fix", 3: "3D fix"}.get(mode, "Unknown")
        status = data.get("status")
        result["status"] = cls._STATUS_MAP.get(status) if status else None
        result["lat"] = data.get("lat")
        result["lon"] = data.get("lon")
        result["alt"] = data.get("altHAE", data.get("alt"))
        result["alt_msl"] = data.get("altMSL")
        result["geoid_sep"] = data.get("geoidSep")
        result["speed"] = data.get("speed")
        result["track"] = data.get("track")
        result["magtrack"] = data.get("magtrack")
        result["magvar"] = data.get("magvar")
        result["climb"] = data.get("climb")
        result["time"] = data.get("time")
        for k in ("epx", "epy", "epv", "eps", "ept", "epd", "epc"):
            result[k] = data.get(k)

    @classmethod
    def _aggregate_sky(cls, sky_list: list[dict], result: dict):
        """Combine one or more SKY messages (gpsd emits one per talker)."""
        # Take the most recently-seen value for each DOP
        for sky in sky_list:
            for dop in ("hdop", "pdop", "vdop", "tdop", "gdop"):
                if sky.get(dop) is not None:
                    result[dop] = sky[dop]

        # Dedupe satellites across messages by (gnssid, PRN)
        merged: dict = {}
        for sky in sky_list:
            for s in sky.get("satellites", []):
                key = (s.get("gnssid"), s.get("PRN"))
                merged[key] = s
        sats = list(merged.values())

        if sats:
            result["satellites_visible"] = len(sats)
            result["satellites_used"] = sum(1 for s in sats if s.get("used"))
        else:
            # Fall back to summed nSat/uSat if the device didn't include
            # a satellites array (e.g., GGA-only NMEA)
            result["satellites_visible"] = sum(s.get("nSat", 0) for s in sky_list) or None
            result["satellites_used"] = sum(s.get("uSat", 0) for s in sky_list) or None

        snr_values = [s["ss"] for s in sats if s.get("ss") is not None and s["ss"] > 0]
        if snr_values:
            result["snr_min"] = min(snr_values)
            result["snr_max"] = max(snr_values)
            result["snr_avg"] = round(sum(snr_values) / len(snr_values), 1)

        constellations: dict = {}
        sat_list = []
        for s in sats:
            name = cls._GNSS_NAMES.get(s.get("gnssid"), "Other")
            c = constellations.setdefault(name, {"used": 0, "visible": 0})
            c["visible"] += 1
            if s.get("used"):
                c["used"] += 1
            sat_list.append({
                "prn": s.get("PRN"),
                "gnss": name,
                "elev": s.get("el"),
                "az": s.get("az"),
                "ss": s.get("ss"),
                "used": bool(s.get("used")),
            })
        result["constellations"] = constellations
        result["satellites"] = sorted(sat_list, key=lambda x: (x["gnss"], x["prn"] or 0))

    def enable_satellite_reporting(self) -> tuple[bool, str]:
        """Enable the standard NMEA set on a u-blox device and persist to flash.

        `ubxtool -e NMEA` turns on the standard set (GGA, GSA, GSV, RMC, VTG)
        in RAM. `ubxtool -p SAVE` then writes the config to BBR/flash so it
        survives a power cycle.
        """
        if not shutil.which("ubxtool"):
            return False, "ubxtool not found (install gpsd-clients)"

        result = self._run(["ubxtool", "-e", "NMEA"], timeout=10)
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "failed"
            return False, f"ubxtool -e NMEA failed: {err}"

        save = self._run(["ubxtool", "-p", "SAVE"], timeout=10)
        if save.returncode != 0:
            return True, "Enabled NMEA (GGA/GSA/GSV/RMC/VTG), but failed to persist — will reset on power cycle."
        return True, "Enabled NMEA (GGA/GSA/GSV/RMC/VTG) and saved to receiver flash."

    def get_logs(self, lines: int = 100) -> str:
        """Get recent gpsd log entries from journald."""
        result = self._run(["journalctl", "-u", self.GPSD_SYSTEMD_SERVICE, "-n", str(lines), "--no-pager"])
        if result.returncode == 0:
            return result.stdout
        return f"Could not retrieve logs: {result.stderr}"

    def restart(self) -> tuple[bool, str]:
        """Restart the gpsd service and reload config."""
        result = self._run(["sudo", "systemctl", "restart", self.GPSD_SYSTEMD_SERVICE], timeout=15)
        if result.returncode == 0:
            self._load_config()
            return True, "gpsd restarted successfully"
        return False, f"Failed to restart gpsd: {result.stderr}"

    def stop(self) -> tuple[bool, str]:
        """Stop the gpsd service."""
        result = self._run(["sudo", "systemctl", "stop", self.GPSD_SYSTEMD_SERVICE], timeout=15)
        if result.returncode == 0:
            return True, "gpsd stopped successfully"
        return False, f"Failed to stop gpsd: {result.stderr}"

    def start(self) -> tuple[bool, str]:
        """Start the gpsd service."""
        result = self._run(["sudo", "systemctl", "start", self.GPSD_SYSTEMD_SERVICE], timeout=15)
        if result.returncode == 0:
            return True, "gpsd started successfully"
        return False, f"Failed to start gpsd: {result.stderr}"

    def set_option(self, flag: str, enabled: bool) -> tuple[bool, str]:
        """Enable or disable a gpsd option and update the config file."""
        if flag not in self.options:
            return False, f"Unknown option: {flag}"

        self.options[flag]["enabled"] = enabled
        success, msg = self._write_config()
        if not success:
            return False, msg
        return True, f"Option {flag} {'enabled' if enabled else 'disabled'}. Restart gpsd to apply."

    def set_devices(self, device_paths: list[str]) -> tuple[bool, str]:
        """Set the GPS device paths in the gpsd config."""
        success, msg = self._write_config(devices=device_paths)
        if not success:
            return False, msg
        return True, f"Devices updated: {', '.join(device_paths) or '(none)'}. Restart gpsd to apply."

    def _write_config(self, devices: list[str] | None = None) -> tuple[bool, str]:
        """Write the current options and devices to /etc/default/gpsd."""
        conf = Path(self.GPSD_CONF_PATH)

        active_flags = [f for f, v in self.options.items() if v["enabled"]]
        options_str = " ".join(active_flags)

        try:
            if conf.exists():
                content = conf.read_text()
            else:
                content = '# Default settings for gpsd\nSTART_DAEMON="true"\nGPSD_OPTIONS=""\nDEVICES=""\nUSBAUTO="true"\n'
        except OSError:
            content = '# Default settings for gpsd\nSTART_DAEMON="true"\nGPSD_OPTIONS=""\nDEVICES=""\nUSBAUTO="true"\n'

        if re.search(r'GPSD_OPTIONS=', content):
            content = re.sub(r'GPSD_OPTIONS="[^"]*"', f'GPSD_OPTIONS="{options_str}"', content)
        else:
            content += f'\nGPSD_OPTIONS="{options_str}"\n'

        if devices is not None:
            devices_str = " ".join(devices)
            if re.search(r'DEVICES=', content):
                content = re.sub(r'DEVICES="[^"]*"', f'DEVICES="{devices_str}"', content)
            else:
                content += f'\nDEVICES="{devices_str}"\n'

        proc = subprocess.run(
            ["sudo", "tee", self.GPSD_CONF_PATH],
            input=content, capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return False, f"Failed to write config: {proc.stderr}"
        return True, "Config updated"

    def get_config(self) -> dict:
        """Read and return the current /etc/default/gpsd config."""
        conf = Path(self.GPSD_CONF_PATH)
        result = {}
        if not conf.exists():
            return {"exists": False}

        try:
            content = conf.read_text()
            result["exists"] = True
            result["raw"] = content

            for key in ["START_DAEMON", "GPSD_OPTIONS", "DEVICES", "USBAUTO"]:
                match = re.search(rf'{key}="([^"]*)"', content)
                if match:
                    result[key.lower()] = match.group(1)
        except OSError as e:
            result["exists"] = False
            result["error"] = str(e)
        return result


# ---------------------------------------------------------------------------
# Async gpsd stream
# ---------------------------------------------------------------------------

class GpsdStream:
    """Persistent async connection to gpsd that fans out updates to subscribers."""

    GPSD_HOST = "127.0.0.1"
    GPSD_PORT = 2947
    RECONNECT_DELAY = 2.0
    # Drop a talker's contribution if we haven't seen a SKY from it in this long.
    TALKER_STALE_S = 15.0

    def __init__(self):
        self.state: dict = self._initial_state()
        self.connected: bool = False
        self.error: str | None = None
        self.last_data_at: float = 0.0
        self._sky_by_talker: dict[str, tuple[float, dict]] = {}
        self._task: asyncio.Task | None = None
        self._subscribers: set[asyncio.Queue] = set()

    @staticmethod
    def _initial_state() -> dict:
        return {
            "fix": "No data", "status": None,
            "lat": None, "lon": None,
            "alt": None, "alt_msl": None, "geoid_sep": None,
            "speed": None, "track": None,
            "magtrack": None, "magvar": None,
            "climb": None, "time": None,
            "epx": None, "epy": None, "epv": None,
            "eps": None, "ept": None, "epd": None, "epc": None,
            "satellites_used": None, "satellites_visible": None,
            "hdop": None, "pdop": None, "vdop": None,
            "tdop": None, "gdop": None,
            "snr_min": None, "snr_max": None, "snr_avg": None,
            "constellations": {},
            "satellites": [],
        }

    async def start(self):
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self):
        while True:
            try:
                await self._stream()
            except asyncio.CancelledError:
                raise
            except (OSError, ConnectionError) as e:
                self.connected = False
                self.error = f"gpsd connection error: {e}"
                self._sky_by_talker.clear()
                self._broadcast()
            except Exception as e:
                self.connected = False
                self.error = f"gpsd stream error: {e!r}"
                self._broadcast()
            await asyncio.sleep(self.RECONNECT_DELAY)

    async def _stream(self):
        reader, writer = await asyncio.open_connection(self.GPSD_HOST, self.GPSD_PORT)
        try:
            try:
                await asyncio.wait_for(reader.readline(), timeout=3.0)
            except asyncio.TimeoutError:
                pass

            writer.write(b'?WATCH={"enable":true,"json":true};\n')
            await writer.drain()

            self.connected = True
            self.error = None
            self._broadcast()

            while True:
                line = await reader.readline()
                if not line:
                    raise ConnectionError("gpsd closed the connection")
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._handle_message(data)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _handle_message(self, data: dict):
        cls = data.get("class")
        if cls == "TPV":
            GpsdManager._parse_tpv(data, self.state)
            self.last_data_at = time.monotonic()
            self._broadcast()
        elif cls == "SKY":
            talker = data.get("talker") or "_default"
            now = time.monotonic()
            self._sky_by_talker[talker] = (now, data)
            cutoff = now - self.TALKER_STALE_S
            self._sky_by_talker = {
                k: v for k, v in self._sky_by_talker.items() if v[0] > cutoff
            }
            sky_list = [v[1] for v in self._sky_by_talker.values()]
            GpsdManager._aggregate_sky(sky_list, self.state)
            self.last_data_at = now
            self._broadcast()

    def snapshot(self) -> dict:
        age = (time.monotonic() - self.last_data_at) if self.last_data_at else None
        return {
            "connected": self.connected,
            "error": self.error,
            "data_age_s": age,
            **self.state,
        }

    def _broadcast(self):
        snap = self.snapshot()
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

manager = GpsdManager()
gps_stream = GpsdStream()
startup_report: dict = {}


def run_startup_checks() -> dict:
    """Run startup checks for gpsd installation and permissions."""
    report = {"ready": True, "issues": []}

    installed, version, gpsd_path = manager.check_installed()
    report["installed"] = installed
    report["version"] = version
    report["gpsd_path"] = gpsd_path

    if not installed:
        report["ready"] = False
        report["issues"].append("gpsd is not installed. Install with: sudo apt install gpsd gpsd-clients")
        return report

    has_perms, perm_issues = manager.check_permissions()
    report["has_permissions"] = has_perms
    if not has_perms:
        report["issues"].extend(perm_issues)

    # Sync options from the running process (if running), otherwise config is
    # already loaded from /etc/default/gpsd during __init__
    result = subprocess.run(
        ["systemctl", "is-active", manager.GPSD_SYSTEMD_SERVICE],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0 and result.stdout.strip() == "active":
        manager.sync_options_from_running()

    # Include configured devices so the frontend can pre-select them
    report["configured_devices"] = manager.get_configured_devices()

    return report


@asynccontextmanager
async def lifespan(app: FastAPI):
    global startup_report
    startup_report = run_startup_checks()
    await gps_stream.start()
    yield
    await gps_stream.stop()


app = FastAPI(title="GPSD Manager", lifespan=lifespan)

templates_dir = Path(__file__).parent / "web"
templates = Jinja2Templates(directory=str(templates_dir))
app.mount("/static", StaticFiles(directory=str(templates_dir)), name="static")


# --- Pages ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# --- API ---

@app.get("/api/startup")
async def api_startup():
    """Return the startup check report."""
    return startup_report


@app.get("/api/status")
async def api_status():
    """Get current gpsd status."""
    status = manager.get_status()
    return asdict(status)


@app.get("/api/gps")
async def api_gps():
    """Return the current GPS snapshot from the streaming connection."""
    return gps_stream.snapshot()


@app.websocket("/ws/gps")
async def ws_gps(websocket: WebSocket):
    """Push GPS state to the client whenever it changes."""
    await websocket.accept()
    q = gps_stream.subscribe()
    try:
        await websocket.send_json(gps_stream.snapshot())
        while True:
            snap = await q.get()
            await websocket.send_json(snap)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        gps_stream.unsubscribe(q)


@app.post("/api/gps/enable-satellite-reporting")
async def api_enable_satellite_reporting():
    """Enable GSV/GSA NMEA sentences on the GPS device (u-blox) and persist."""
    ok, msg = manager.enable_satellite_reporting()
    return {"success": ok, "message": msg}


@app.post("/api/start")
async def api_start():
    """Start gpsd."""
    ok, msg = manager.start()
    return {"success": ok, "message": msg}


@app.post("/api/stop")
async def api_stop():
    """Stop gpsd."""
    ok, msg = manager.stop()
    return {"success": ok, "message": msg}


@app.post("/api/restart")
async def api_restart():
    """Restart gpsd."""
    ok, msg = manager.restart()
    return {"success": ok, "message": msg}


@app.get("/api/logs")
async def api_logs(lines: int = 100):
    """Get recent gpsd logs."""
    logs = manager.get_logs(lines=lines)
    return {"logs": logs}


@app.get("/api/devices")
async def api_devices():
    """Discover available GPS devices."""
    devices = manager.discover_devices()
    return {"devices": devices}


@app.post("/api/devices")
async def api_set_devices(request: Request):
    """Set the GPS device paths."""
    body = await request.json()
    paths = body.get("devices", [])
    ok, msg = manager.set_devices(paths)
    return {"success": ok, "message": msg}


@app.get("/api/options")
async def api_options():
    """Get gpsd options."""
    return {"options": manager.options}


@app.post("/api/options")
async def api_set_option(request: Request):
    """Toggle a gpsd option."""
    body = await request.json()
    flag = body.get("flag")
    enabled = body.get("enabled", False)
    ok, msg = manager.set_option(flag, enabled)
    return {"success": ok, "message": msg}


@app.post("/api/options/save")
async def api_save_options():
    """Save current options to /etc/default/gpsd."""
    ok, msg = manager._write_config()
    return {"success": ok, "message": msg if not ok else "Options saved to config. Restart gpsd to apply."}


@app.get("/api/config")
async def api_config():
    """Get the raw gpsd config file."""
    return manager.get_config()


if __name__ == "__main__":
    import uvicorn
    host = "0.0.0.0"
    port = 8000
    print(f"GPSD Manager starting — http://{host}:{port}")
    uvicorn.run("gpsd_manager:app", host=host, port=port, reload=True, log_level="warning")
