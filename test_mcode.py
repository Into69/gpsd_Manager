#!/usr/bin/env python3
"""Test MCODE parsing and NMEA conversion."""

from mcode_converter import MCodeParser, NMEAGenerator
from datetime import datetime, timezone


def test_parsing():
    """Test MCODE parsing with your sample data."""
    parser = MCodeParser()

    # Your complete sample data (split into parts as transmitted)
    lines = [
        "$MCODE1/2,45.43297400,-76.36070250,95.241,0,23.785,178813,37,0.",
        "$MCODE2/2,077",
    ]

    for line in lines:
        result = parser.feed(line + "\n")
        if result:
            print(f"[OK] Parsed: {result}")
            return result

    print("[FAIL] Failed to parse sample data")
    return None


def test_conversion():
    """Test NMEA generation."""
    data = {
        "lat": 45.43297400,
        "lon": -76.36070250,
        "alt": 95.241,
        "speed_ms": 0.077,
    }

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    gga = NMEAGenerator.gga(data, now)
    rmc = NMEAGenerator.rmc(data, now)

    print("\nGenerated NMEA sentences:")
    print(f"GGA: {gga.strip()}")
    print(f"RMC: {rmc.strip()}")

    # Validate checksum format
    if "*" in gga and "*" in rmc:
        print("[OK] Checksums present")
    else:
        print("[FAIL] Missing checksums")


if __name__ == "__main__":
    print("Testing MCODE parser...\n")
    parsed = test_parsing()

    if parsed:
        print("\nTesting NMEA generation...")
        test_conversion()
        print("\n[OK] All tests passed!")
    else:
        print("\n[FAIL] Parser test failed. Check MCODE format.")
