"""GPSD Manager - FastAPI web application for managing a local gpsd instance."""

import glob
import json
import os
import re
import shutil
import subprocess
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
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

    def _load_config(self):
        """Load saved options from /etc/default/gpsd if it exists."""
        conf = Path(self.GPSD_CONF_PATH)
        if not conf.exists():
            return
        try:
            content = conf.read_text()
            match = re.search(r'GPSD_OPTIONS="([^"]*)"', content)
            if match:
                saved_flags = match.group(1).split()
                for flag in saved_flags:
                    if flag in self.options:
                        self.options[flag]["enabled"] = True
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

        # Reset all options, then enable those present in the cmdline
        for flag in self.options:
            self.options[flag]["enabled"] = False
        for arg in args:
            if arg in self.options:
                self.options[arg]["enabled"] = True

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

        result = self._run(["groups"])
        if result.returncode == 0:
            groups = result.stdout.strip().split()
            if "dialout" not in groups:
                issues.append("User not in 'dialout' group - may not be able to access serial GPS devices")

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

    def get_logs(self, lines: int = 100) -> str:
        """Get recent gpsd log entries from journald."""
        result = self._run(["journalctl", "-u", self.GPSD_SYSTEMD_SERVICE, "-n", str(lines), "--no-pager"])
        if result.returncode == 0:
            return result.stdout
        return f"Could not retrieve logs: {result.stderr}"

    def restart(self) -> tuple[bool, str]:
        """Restart the gpsd service."""
        result = self._run(["sudo", "systemctl", "restart", self.GPSD_SYSTEMD_SERVICE], timeout=15)
        if result.returncode == 0:
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
# FastAPI application
# ---------------------------------------------------------------------------

manager = GpsdManager()
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

    if startup_report["installed"]:
        print(f"[startup] gpsd found: v{startup_report['version']} at {startup_report['gpsd_path']}")
    else:
        print("[startup] WARNING: gpsd is NOT installed")

    if startup_report["issues"]:
        for issue in startup_report["issues"]:
            print(f"[startup] ISSUE: {issue}")

    yield


app = FastAPI(title="GPSD Manager", lifespan=lifespan)

templates_dir = Path(__file__).parent / "web"
templates = Jinja2Templates(directory=str(templates_dir))


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


@app.get("/api/config")
async def api_config():
    """Get the raw gpsd config file."""
    return manager.get_config()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("gpsd_manager:app", host="0.0.0.0", port=8000, reload=True)
