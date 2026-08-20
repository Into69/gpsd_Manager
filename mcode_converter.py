"""Convert MCODE serial GPS data to NMEA format for gpsd consumption."""

import json
import os
import platform
import re
import serial
import socket
import sys
import time
from datetime import datetime
from pathlib import Path


class MCodeParser:
    """Parse MCODE GPS sentences."""

    def __init__(self):
        self.buffer = ""
        self.current_mcode = {}

    def feed(self, data: str) -> dict | None:
        """Feed data and return parsed MCODE dict when complete, or None."""
        self.buffer += data

        # Look for complete MCODE sentences (multi-part)
        # Pattern: $MCODE1/2 or $MCODE2/2
        match = re.search(r'\$MCODE(\d+)/(\d+),(.+?)(?=\$|$)', self.buffer)
        if not match:
            return None

        part_num = int(match.group(1))
        total_parts = int(match.group(2))
        part_data = match.group(3).rstrip()

        # Store the part
        self.current_mcode[part_num] = part_data

        # Clean up buffer
        self.buffer = self.buffer[match.end():]

        # If we have all parts, return parsed data
        if len(self.current_mcode) == total_parts:
            return self._parse_complete()

        return None

    def _parse_complete(self) -> dict | None:
        """Parse complete MCODE data from all parts."""
        try:
            # Join all parts and split by comma
            full_data = "".join(self.current_mcode[i] for i in sorted(self.current_mcode.keys()))
            fields = full_data.split(",")

            if len(fields) < 4:
                self.current_mcode.clear()
                return None

            result = {
                "lat": float(fields[0]),
                "lon": float(fields[1]),
                "alt": float(fields[2]),
                "speed_ms": float(fields[3]) if len(fields) > 3 else 0,
            }

            self.current_mcode.clear()
            return result
        except (ValueError, IndexError):
            self.current_mcode.clear()
            return None


class NMEAGenerator:
    """Generate standard NMEA sentences from MCODE data."""

    @staticmethod
    def _checksum(sentence: str) -> str:
        """Calculate NMEA checksum."""
        checksum = 0
        for char in sentence:
            checksum ^= ord(char)
        return f"{checksum:02X}"

    @staticmethod
    def _dms_from_decimal(decimal_degrees: float, is_lat: bool) -> tuple[str, str]:
        """Convert decimal degrees to DDmm.mmmm format and return (dms, dir)."""
        is_negative = decimal_degrees < 0
        abs_val = abs(decimal_degrees)

        degrees = int(abs_val)
        minutes = (abs_val - degrees) * 60

        if is_lat:
            direction = "S" if is_negative else "N"
            dms = f"{degrees:02d}{minutes:07.4f}"
        else:
            direction = "W" if is_negative else "E"
            dms = f"{degrees:03d}{minutes:07.4f}"

        return dms, direction

    @classmethod
    def gga(cls, data: dict, timestamp: datetime | None = None) -> str:
        """Generate $GPGGA sentence (position fix)."""
        if timestamp is None:
            timestamp = datetime.utcnow()

        time_str = timestamp.strftime("%H%M%S")
        lat_dms, lat_dir = cls._dms_from_decimal(data["lat"], is_lat=True)
        lon_dms, lon_dir = cls._dms_from_decimal(data["lon"], is_lat=False)

        # Fix quality: 1=GPS fix, 0=no fix
        fix_quality = 1 if data.get("lat") and data.get("lon") else 0

        # Build sentence (without $ and checksum)
        sentence = f"GPGGA,{time_str},{lat_dms},{lat_dir},{lon_dms},{lon_dir},{fix_quality},00,99.99,{data['alt']:.1f},M,0,M,,"

        checksum = cls._checksum(sentence)
        return f"${sentence}*{checksum}\r\n"

    @classmethod
    def rmc(cls, data: dict, timestamp: datetime | None = None) -> str:
        """Generate $GPRMC sentence (position, speed, course)."""
        if timestamp is None:
            timestamp = datetime.utcnow()

        time_str = timestamp.strftime("%H%M%S")
        date_str = timestamp.strftime("%d%m%y")

        # Status: A=active, V=void
        status = "A" if data.get("lat") and data.get("lon") else "V"

        lat_dms, lat_dir = cls._dms_from_decimal(data["lat"], is_lat=True)
        lon_dms, lon_dir = cls._dms_from_decimal(data["lon"], is_lat=False)

        # Convert speed from m/s to knots (1 m/s = 1.94384 knots)
        speed_knots = data.get("speed_ms", 0) * 1.94384

        # Build sentence
        sentence = f"GPRMC,{time_str},{status},{lat_dms},{lat_dir},{lon_dms},{lon_dir},{speed_knots:.2f},0,{date_str},,"

        checksum = cls._checksum(sentence)
        return f"${sentence}*{checksum}\r\n"


class MCODEConverter:
    """Main converter: reads MCODE from serial, outputs NMEA."""

    def __init__(self, port: str, baudrate: int = 9600, output_path: str | None = None, output_mode: str = "auto"):
        self.port = port
        self.baudrate = baudrate
        self.output_path = output_path
        self.output_mode = output_mode  # "auto", "file", "pipe", "tcp"
        self.parser = MCodeParser()
        self.running = False
        self.is_windows = platform.system() == "Windows"
        self.debug_file = self._get_debug_file_path()

    def run(self):
        """Main loop: read from serial, convert, output."""
        try:
            ser = serial.Serial(self.port, self.baudrate, timeout=1)
        except serial.SerialException as e:
            print(f"Failed to open {self.port}: {e}", file=sys.stderr)
            return

        # Send enable command to the device
        try:
            enable_cmd = "mcode atak enable\n"
            ser.write(enable_cmd.encode("utf-8"))
            ser.flush()
            print(f"Sent enable command: {enable_cmd.strip()}", file=sys.stderr)
            time.sleep(0.5)
        except Exception as e:
            print(f"Warning: Failed to send enable command: {e}", file=sys.stderr)

        # Open output based on platform and mode
        output = None
        try:
            output = self._open_output()
        except Exception as e:
            print(f"Failed to open output: {e}", file=sys.stderr)
            ser.close()
            return

        try:
            self.running = True
            output_desc = self.output_path or "stdout"
            print(f"MCODE converter: reading from {self.port}, outputting to {output_desc}", file=sys.stderr)

            while self.running:
                try:
                    if ser.in_waiting:
                        data = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                        parsed = self.parser.feed(data)

                        if parsed:
                            now = datetime.now(datetime.now().astimezone().tzinfo)
                            gga = NMEAGenerator.gga(parsed, now)
                            rmc = NMEAGenerator.rmc(parsed, now)

                            # Store raw MCODE for debugging
                            self._write_debug_info(data, parsed, gga, rmc)

                            try:
                                if output:
                                    output.write(gga)
                                    output.write(rmc)
                                    output.flush()
                            except (BrokenPipeError, OSError):
                                pass
                    else:
                        time.sleep(0.01)
                except Exception as e:
                    print(f"Conversion error: {e}", file=sys.stderr)
                    time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            ser.close()
            if output and output != sys.stdout:
                try:
                    output.close()
                except OSError:
                    pass

    def _open_output(self):
        """Open output stream (file-based for simplicity)."""
        if not self.output_path:
            return sys.stdout

        print(f"Opening output file: {self.output_path}", file=sys.stderr)
        try:
            f = open(self.output_path, "w", buffering=1)
            # Set world-readable permissions on Linux
            if not self.is_windows:
                try:
                    os.chmod(self.output_path, 0o666)
                except OSError:
                    pass
            return f
        except Exception as e:
            print(f"Failed to open output: {e}", file=sys.stderr)
            return sys.stdout

    def _get_debug_file_path(self) -> str:
        """Get path for debug file."""
        temp_dir = Path(os.environ.get("TEMP") if self.is_windows else "/tmp")
        return str(temp_dir / "mcode_debug.json")

    def _write_debug_info(self, raw_line: str, parsed: dict, nmea_gga: str, nmea_rmc: str):
        """Write debug info to JSON file."""
        try:
            debug_info = {
                "timestamp": datetime.now().isoformat(),
                "raw_mcode": raw_line,
                "parsed_fields": parsed,
                "nmea_gga": nmea_gga.strip(),
                "nmea_rmc": nmea_rmc.strip(),
            }
            with open(self.debug_file, "w") as f:
                json.dump(debug_info, f, indent=2)
        except Exception:
            pass  # Silently ignore debug write errors


if __name__ == "__main__":
    import argparse

    default_port = "COM3" if platform.system() == "Windows" else "/dev/ttyUSB0"

    parser = argparse.ArgumentParser(description="Convert MCODE GPS to NMEA")
    parser.add_argument("--port", default=default_port, help=f"Serial port (default: {default_port})")
    parser.add_argument("--baudrate", type=int, default=9600, help="Baud rate (default: 9600)")
    parser.add_argument("--output", help="Output path (FIFO on Unix, file/pipe on Windows)")
    parser.add_argument("--mode", default="auto", choices=["auto", "file", "pipe", "tcp"],
                        help="Output mode (default: auto)")

    args = parser.parse_args()

    converter = MCODEConverter(args.port, args.baudrate, args.output, args.mode)
    converter.run()
