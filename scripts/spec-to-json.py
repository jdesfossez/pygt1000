#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def convert_to_json(data):
    result = {}
    for line in data:
        # Remove leading and trailing whitespace
        line = line.strip()

        # Split the line by the '|' character
        parts = line.split("|")

        if len(parts) < 3:
            continue  # Skip lines that don't match the expected format

        # Extract address and name/table parts
        address_str = parts[1].strip()
        name_table_str = parts[2].strip()

        # Convert address to list of hex values
        address = [int(x, 16) for x in address_str.split()]
        hex_address = "[{}]".format(", ".join(hex(x) for x in address))

        # Split name_table_str by the '[' character
        name, table = name_table_str.split("[")
        name = name.strip().replace("(*)", "")
        table = table.strip("] ")
        # The options are the same for each PatchFx
        for i in range(1, 5):
            for effect in ["Dist", "Ns", "Eq", "Fx", "Delay"]:
                table = table.replace(f"Patch{effect}{i}", f"Patch{effect}")
        for i in ["A", "B"]:
            table = table.replace(f"PatchPreamp{i}", "PatchPreamp")

        # Build the dictionary entry
        result[name] = {"address": address, "table": table, "hex_address": hex_address}

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
        data = file.readlines()

    # Convert the data
    converted_data = convert_to_json(data)

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
