#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def parse_value_range(value_range_str):
    # Extract numbers within parentheses and convert them to a list of integers
    value_range = [int(x) for x in value_range_str.strip("()").split("-")]
    return value_range


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


def process_data(lines):
    result = {}
    current_name = None
    current_value_range = None
    offset = 0
    conflict_count = {}

    for line in lines:
        # Remove leading and trailing whitespace
        line = line.strip()

        print(line)
        # FIXME: these are offsets that span multiple bytes, need to find a solution
        if line.startswith("|# "):
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
            for i in offset_txt.split(" "):
                offset_bytes.append(int(i, 16))

            name_with_range = parts[3].strip()
            if not "(" in name_with_range:
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
            # Prepare a dictionary for this name
            result[name] = {
                "offset": offset_bytes,
                "value_range": value_range,
                "values": {},
            }
            current_name = name
            current_value_range = value_range
            offset = 0

    return result


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
