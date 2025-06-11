#!/bin/bash

# Setup script for systemd service management permissions
# This script configures sudo permissions for the tsconfig application

set -e

echo "Setting up systemd service management permissions for tsconfig..."

# Check if running as root
if [[ $EUID -eq 0 ]]; then
    echo "This script should not be run as root. Please run as your regular user."
    exit 1
fi

# Get current username
USERNAME=$(whoami)
echo "Current user: $USERNAME"

# Create sudoers file
SUDOERS_FILE="/tmp/tsconfig-sudoers"
echo "Creating sudoers configuration..."

cat > "$SUDOERS_FILE" << EOF
# Sudo configuration for tsconfig systemd service management
# Generated on $(date)

# Allow the $USERNAME user to manage systemd services
# without password prompts for the configured services

$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl start chrony
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl stop chrony
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl restart chrony
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl start wittypid
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl stop wittypid
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl restart wittypid
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl start wg-quick@wireguard
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl stop wg-quick@wireguard
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl restart wg-quick@wireguard
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl start mosquitto
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl stop mosquitto
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl restart mosquitto
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl start mqttutil
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl stop mqttutil
$USERNAME ALL=(ALL) NOPASSWD: /bin/systemctl restart mqttutil
$USERNAME ALL=(ALL) NOPASSWD: /sbin/reboot
EOF

echo "Validating sudoers syntax..."
if sudo visudo -c -f "$SUDOERS_FILE"; then
    echo "Sudoers syntax is valid."
    
    echo "Installing sudoers configuration..."
    sudo cp "$SUDOERS_FILE" /etc/sudoers.d/tsconfig
    sudo chmod 440 /etc/sudoers.d/tsconfig
    sudo chown root:root /etc/sudoers.d/tsconfig
    
    echo "Cleaning up temporary file..."
    rm "$SUDOERS_FILE"
    
    echo "✅ Setup completed successfully!"
    echo ""
    echo "The tsconfig application can now manage the following systemd services:"
    echo "  - chrony"
    echo "  - wittypid"
    echo "  - wg-quick@wireguard"
    echo "  - mosquitto"
    echo "  - mqttutil"
    echo ""
    echo "Additional permissions:"
    echo "  - System reboot"
    echo ""
    echo "You can test the configuration by running:"
    echo "  sudo systemctl status chrony"
    
else
    echo "❌ Sudoers syntax validation failed!"
    echo "Please check the configuration manually."
    rm "$SUDOERS_FILE"
    exit 1
fi 