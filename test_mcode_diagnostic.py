#!/usr/bin/env python3
"""Diagnostic test to show all MCODE fields and their parsed values."""

from mcode_converter import MCodeParser


def test_with_sample_data():
    """Parse your sample data and show all fields."""
    parser = MCodeParser()

    # Your complete sample data as transmitted
    lines = [
        "$MCODE1/2,45.43297400,-76.36070250,95.241,0,23.785,178813,37,0.",
        "$MCODE2/2,077",
    ]

    for line in lines:
        result = parser.feed(line + "\n")
        if result:
            print("Parsed MCODE data:")
            for key, val in result.items():
                print(f"  {key}: {val}")
            return

    print("[FAIL] Could not parse sample data")


def test_raw_field_breakdown():
    """Show the raw field breakdown of the complete data."""
    complete_data = "45.43297400,-76.36070250,95.241,0,23.785,178813,37,0.077"
    fields = complete_data.split(",")

    print("\nRaw field breakdown (comma-separated):")
    for i, field in enumerate(fields):
        print(f"  Field {i} (index {i}): {field}")

    print("\nCurrent parser mapping:")
    print(f"  Field 0 (lat):      {fields[0]}")
    print(f"  Field 1 (lon):      {fields[1]}")
    print(f"  Field 2 (alt):      {fields[2]}")
    print(f"  Field 3 (speed):    {fields[3]}")
    print(f"  Field 4 (unused):   {fields[4] if len(fields) > 4 else 'N/A'}")
    print(f"  Field 5 (unused):   {fields[5] if len(fields) > 5 else 'N/A'}")
    print(f"  Field 6 (unused):   {fields[6] if len(fields) > 6 else 'N/A'}")
    print(f"  Field 7 (unused):   {fields[7] if len(fields) > 7 else 'N/A'}")

    print("\nNOTE: If your field mapping is different, let me know and I'll update the parser!")


if __name__ == "__main__":
    test_with_sample_data()
    test_raw_field_breakdown()
