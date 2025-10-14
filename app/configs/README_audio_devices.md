# Static Audio Devices Configuration

## Purpose

When `tsconfig` runs in **server mode**, it cannot query actual hardware for available audio devices. Instead, it uses a static list of devices defined in `audio_devices_static.yml`.

## Configuration File

**File**: `audio_devices_static.yml`

This file defines the audio input and output devices that will be shown in the Soundscapepipe configuration UI when running in server mode.

## Structure

```yaml
input:
  - index: 0                              # Unique device index
    name: "Device Name: Audio (hw:2,0)"  # Display name (can include hardware ID)
    max_input_channels: 2                 # Number of input channels
    max_output_channels: 0                # Number of output channels (0 for input-only)
    default_sample_rate: 384000           # Maximum supported sample rate
    hostapi: 0                            # Host API index (typically 0)
    is_default: true                      # Whether this is the default input device

output:
  - index: 10                             # Unique device index
    name: "Output Device Name"            # Display name
    max_input_channels: 0                 # Number of input channels (0 for output-only)
    max_output_channels: 2                # Number of output channels
    default_sample_rate: 48000            # Maximum supported sample rate
    hostapi: 0                            # Host API index
    is_default: true                      # Whether this is the default output device

default_input: 0    # Index of default input device
default_output: 10  # Index of default output device
```

## Customization

To customize the device list for your deployment:

1. **Identify your hardware**: On a real device, run tsconfig in normal mode (not server mode) to see the actual detected devices
2. **Copy device information**: Note the device names, sample rates, and channel counts
3. **Edit the YAML file**: Update `audio_devices_static.yml` with your specific devices
4. **Restart tsconfig**: The new device list will be loaded on the next server mode startup

## Common Device Types

### trackIT Analog Frontend
High-quality audio frontend for wildlife monitoring:
- Sample rate: up to 384 kHz
- Channels: 2 (stereo)

### USB Audio Devices
Generic USB audio interfaces:
- Sample rate: typically 48-192 kHz
- Channels: 1-2

### Raspberry Pi Audio
Built-in audio output:
- Sample rate: typically 48 kHz
- Channels: 2 (stereo)

## Notes

- **Device names** should match the `input_device_match` and `output_device_match` fields in soundscapepipe configuration
- The device name before the colon (e.g., "trackIT Analog Frontend") is what gets stored in the config
- **Index values** should be unique across all devices
- **Default devices** are pre-selected when creating new configurations
- In server mode, users can still manually type device names if they're not in the list


