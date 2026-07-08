#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def parse_value_range(value_range_str):
    # Extract numbers within parentheses and convert them to a list of integers
    value_range = [int(x) for x in value_range_str.strip("()").split("-")]
    return value_range


def offset_to_int(offset_bytes):
    # Offsets in the option tables are two big-endian bytes.
    return int.from_bytes(bytes(offset_bytes), byteorder="big")


# FIXME: delay ranges are weird: you get a range and then names:
# 1ms - 2000ms, 32ndNote, Triplet16thNote, [...]
# so we need to expand to:
# 1ms, 2ms, [...], 2000ms, 32ndNote, Triplet16thNote, [...]
# same in PatchFxCVibe
def expand_range(begin, end, unit, divide=1):
    all_names = "| | | "
    for i in range(begin, end + 1):
        if divide != 1:
            value = i / divide
        else:
            value = i
        all_names += f"{value}{unit}, "
    all_names += "|"
    return all_names


SPAN_RE = None  # compiled lazily (re imported below main historically)


def fix_span_tails(result):
    """Fix "range span + named tail" value lists (the old FIXME).

    Several params list their values as a numeric span followed by named
    entries, e.g. delay TIME (1 - 2018): "1ms - 2000ms, 32ndNote, ...".
    The streaming pass enumerates every comma item from range-min, so the
    span eats ONE slot and 32ndNote lands on 2 -- wrong: 2 is 2ms. The
    correct, unit-agnostic rule: the T named tail entries occupy the LAST T
    values of the range; the span covers everything below. So for TIME the
    notes are 2001..2018 and the numeric region 1..2000 carries no names
    (decode falls back to the raw number).

    Applied as a post-pass: spot a span-shaped first value key, drop it, and
    renumber the remaining (insertion-ordered) names onto the range tail.
    """
    import re
    span_re = re.compile(r"^[\d.]+\s*[a-zA-Z]*\s*-\s*[\d.]+\s*[a-zA-Z]+$")
    for name, entry in result.items():
        values = entry.get("values")
        if not values:
            continue
        keys = list(values.keys())
        if not span_re.match(keys[0]):
            continue
        if len(keys) < 2:      # span only, no named tail: numeric fallback
            entry["values"] = {}
            continue
        tail = keys[1:]
        lo, hi = entry["value_range"]
        start = hi - len(tail) + 1
        if start <= lo:
            continue  # tail would not fit above the span; leave untouched
        entry["values"] = {n: start + i for i, n in enumerate(tail)}
    return result


def process_data(lines):
    result = {}
    current_name = None
    current_value_range = None
    offset = 0
    conflict_count = {}
    # A "|# AA BB |" line marks the first address of a multi-byte (nibblised)
    # field: each consecutive address carries one 4-bit nibble (0000 xxxx),
    # big-endian, and the field is named on its LAST address. We stash the start
    # here and, when the named row lands, record that param's offset as the start
    # address with a "bytes" width = (last - start + 1). Single-byte params leave
    # this None and get no "bytes" key (width 1 is the default).
    pending_start = None

    for line in lines:
        # Remove leading and trailing whitespace
        line = line.strip()

        print(line)
        # Multi-byte field start marker: remember the first (most significant)
        # nibble's address; the named row below closes the field.
        if line.startswith("|# "):
            start_txt = line.split("|")[1].strip().lstrip("#").strip()
            pending_start = [int(x, 16) for x in start_txt.split()]
            continue
        if line.startswith("| : | | |"):
            continue
        if current_name and line.startswith("| | |"):
            # EQ range is not written as a CSV list, fix it manually here
            if line == "| | | -20 - 0 - +20 [dB] |":
                line = expand_range(-20, 20, "dB")
            elif line == "| | | -50 - 50 |":
                line = expand_range(-50, 50, "")
            elif line == "| | | -10 - 10 |":
                line = expand_range(-10, 10, "")
            elif line == "| | | 0.1s, 0.2s - 10.0s |":
                line = expand_range(1, 100, "s", divide=10)
            elif line == "| | | -50 - -1, 0, +1 - +50 |":
                line = expand_range(-50, 50, "")
            # PRE DELAY in Reverb only has the unit as a value
            if line.split("|")[3].strip() == "[ms]":
                continue
            # This line contains a list of options
            options = line.split("|")[3].split(",")
            options = [opt.strip() for opt in options if opt.strip()]

            # Map each option to a corresponding value within the range
            start_value = current_value_range[0] + offset
            for i, option in enumerate(options):
                result[current_name]["values"][option] = start_value + i
                offset += 1
        elif line.startswith("|") and len(line.split("|")) > 3:
            # This line contains the address, ignore part, name, and value range
            parts = line.split("|")
            offset_txt = parts[1].strip()
            offset_bytes = []
            for i in offset_txt.split():  # whitespace-agnostic (specs vary)
                offset_bytes.append(int(i, 16))

            name_with_range = parts[3].strip()
            if "(" not in name_with_range:
                continue
            name, value_range = name_with_range.rsplit("(", 1)
            name = name.strip()
            # Only the PatchFx table has non-standard names from ON/OFF and TYPE
            # let's fix that here to avoid special cases in the code.
            if name == "FX SW":
                name = "SW"
            elif name == "FX1 TYPE":
                name = "TYPE"
            value_range = parse_value_range(value_range)

            # Some option names have duplicates in the spec...
            # for example in PatchEq, LEVEL is there twice, one
            # for each type for some reason
            if name in result:
                if name in conflict_count:
                    conflict_count[name] += 1
                else:
                    conflict_count[name] = 1
                print(
                    f"WARNING: conflicting name {name}, storing as {name}{conflict_count[name]}"
                )
                name = f"{name}{conflict_count[name]}"
            # A multi-byte field is addressed at its first nibble; the named row
            # sits on the last. Record the start offset + the byte width so the
            # encoder writes / the decoder reads all N nibbles.
            if pending_start is not None:
                width = offset_to_int(offset_bytes) - offset_to_int(pending_start) + 1
                entry = {
                    "offset": pending_start,
                    "value_range": value_range,
                    "bytes": width,
                    "values": {},
                }
                pending_start = None
            else:
                entry = {
                    "offset": offset_bytes,
                    "value_range": value_range,
                    "values": {},
                }
            # Prepare a dictionary for this name
            result[name] = entry
            current_name = name
            current_value_range = value_range
            offset = 0

    return fix_span_tails(result)


def main():
    parser = argparse.ArgumentParser(description="Convert input file to JSON format.")
    parser.add_argument("input_file", type=str, help="Path to the input file")
    parser.add_argument(
        "-o", "--output_file", type=str, help="Path to the output JSON file"
    )

    args = parser.parse_args()

    # Use pathlib to handle file paths
    input_path = Path(args.input_file)

    # Check if input file exists
    if not input_path.is_file():
        print(f"Error: The file '{input_path}' does not exist.")
        return

    # Read the input file
    with input_path.open("r") as file:
        lines = file.readlines()

    # Process the data
    converted_data = process_data(lines)

    # Convert to JSON string
    json_output = json.dumps(converted_data, indent=4)

    # Determine output path
    if args.output_file:
        output_path = Path(args.output_file)
    else:
        output_path = input_path.with_suffix(".json")

    # Write the output to a file
    with output_path.open("w") as output_file:
        output_file.write(json_output)
        print(f"JSON output saved to '{output_path}'")


if __name__ == "__main__":
    main()
