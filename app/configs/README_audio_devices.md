# Audio Devices Configuration

## Purpose

This configuration file defines the audio devices that should be available in the Soundscapepipe UI. It is used in both operational modes:

### Tracker Mode (default)

Runs directly on sensor station hardware with full access to audio devices and system resources.

- **Config validation**: The configuration file is validated against actual hardware
- **Only present devices shown**: Only devices from the config that are physically connected are displayed
- **Live updates**: Configuration can be updated while the service is running

### Server Mode

Runs remotely to manage multiple sensor station configurations without hardware access.

- **No validation**: All devices from the configuration are shown without checking hardware
- **Static list**: Used when actual device detection is not possible

## Configuration File

**File**: `audio_devices.yml`

This file defines the audio input and output devices that will be shown in the Soundscapepipe configuration UI when running in server mode.

## Structure

```yaml
input:
  - name: "Device Name: Audio (hw:2,0)"  # Display name (can include hardware ID)
    max_input_channels: 2                 # Number of input channels
    default_sample_rate: 384000           # Maximum supported sample rate
    is_default: true                      # Whether this is the default input device

output:
  - name: "Output Device Name"            # Display name
    max_output_channels: 2                # Number of output channels
    default_sample_rate: 48000            # Maximum supported sample rate
    is_default: true                      # Whether this is the default output device
```

## Customization

To customize the device list for your deployment:

1. **Identify your hardware**: On a real device with devices connected, query the system to see available devices
2. **Copy device information**: Note the device names (before the colon), sample rates, and channel counts
3. **Edit the YAML file**: Update `audio_devices.yml` with your specific devices
4. **Automatic reload**: The configuration is read fresh on every request - changes take effect immediately (no restart required)

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

### Device Naming

- **Device names** should match the beginning of the actual hardware device name
- Example: Config name "trackIT Analog Frontend" matches "trackIT Analog Frontend: Audio (hw:2,0)"
- The config name (before the colon) is what gets stored in soundscapepipe configuration

### Device Matching

- **Tracker mode**: Only devices present on the hardware are shown (config is validated)
- **Server mode**: All configured devices are shown (no validation)
- **Indices**: Device indices are assigned dynamically based on actual hardware indices

### Default Devices

- Marked with `is_default: true` in the configuration
- Pre-selected when creating new configurations
- In tracker mode, system default device overrides config default

### Manual Device Entry

- Users can always manually type device names if they're not in the list
- Useful for devices not yet added to the configuration


