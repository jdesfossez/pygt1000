#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def process_data(lines):
    result = {}

    for line in lines:
        # Remove all whitespaces and the first |
        line = line.replace(" ", "").strip()[1:].split("|")
        print(line)
        result[line[0]] = line[1]
        # Store in the 2 directions to make lookups easy
        result[line[1]] = line[0]
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
