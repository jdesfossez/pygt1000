# pygt1000

pygt1000 is a Python library for interacting with the BOSS GT-1000 and
GT-1000CORE guitar/bass effects processors over MIDI. This library provides an
easy-to-use API for controlling and managing the settings of your
GT-1000/GT-1000CORE, enabling both real-time interaction and more complex
automation tasks.

## Features

- Send and receive MIDI messages to/from GT-1000/GT-1000CORE
- Manage and modify effect parameters
- Automate complex sequences of commands
- Retrieve and process device states and data
- Construct MIDI messages using option names instead of hexadecimal codes

## Installation

To install the pygt1000 library, you can use pip:

```bash
pip install pygt1000
```


Or for local development, use `poetry`:

```bash
git clone https://github.com/jdesfossez/pygt1000.git
cd pygt1000
poetry install
```

## Usage

Here’s a basic example of how to use the pygt1000 library to interact with your
GT-1000:

```python
from pygt1000 import GT1000

# Initialize the GT1000 object
gt1000 = GT1000()

# Open the MIDI ports
gt1000.open_ports()
# By default it looks for a MIDI port starting with "GT-1000:GT-1000 MIDI 1"
# But you can override the portname option here

# Send a command to the GT-1000
gt1000.send_command(...)

# Close the MIDI ports when done
gt1000.close_ports()
```
## Crafting messages

Most of the [MIDI specifications from BOSS](https://www.boss.info/global/support/by_product/gt-1000/owners_manuals/564517d2-518e-469e-b50b-2bdca359d24d/
) have been imported as JSON files in the specs/ directory. This allows the user to construct MIDI messages using option names rather than manually crafting hexadecimal messages. For example:

```python
gt = GT1000()
start_section = gt._get_start_section("fx", "1")
message = gt.build_dt_message(start_section, "fx1", "SW", "ON")
gt1000.send_message(message)
```

The JSON files are created by parsing the text from the specification (one text
file by table). Some consistency fixes are performed by the parsing
[scripts](./scripts/). There are still some unhandled cases like messages options
with a very wide range requiring multiple bytes to set the value, but most features
work with a single byte operation.
