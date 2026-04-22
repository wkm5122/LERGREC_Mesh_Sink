#!/usr/bin/env bash
# =============================================================================
# setup_pi.sh — One-shot setup for LERGREC Sink Data Handler on Raspberry Pi 4
#
# Run once from the project directory:
#   chmod +x setup_pi.sh
#   ./setup_pi.sh
#
# After this script completes the handler will:
#   • Start automatically at every boot
#   • Restart itself if it crashes
#   • Be accessible at http://<pi-ip>:5000
#
# To adjust the serial port or baud rate edit the PI_* constants near the top
# of sink_handler.py before running this script.
# =============================================================================

set -euo pipefail

INSTALL_DIR="/home/pi/sink_handler"
SERVICE_NAME="sink_handler"
SERVICE_FILE="sink_handler.service"
SYSTEMD_DIR="/etc/systemd/system"

echo ""
echo "======================================================"
echo " LERGREC Sink Data Handler — Raspberry Pi 4 Setup"
echo "======================================================"
echo ""

# --------------------------------------------------------------------------
# 1. Python dependencies
# --------------------------------------------------------------------------
echo "[1/5] Installing Python dependencies (pyserial, flask) ..."
pip3 install --break-system-packages pyserial flask
echo "      Done."
echo ""

# --------------------------------------------------------------------------
# 2. Serial port permissions
# --------------------------------------------------------------------------
echo "[2/5] Granting serial port access (adding '$USER' to dialout group) ..."
sudo usermod -aG dialout "$USER"
echo "      Done. (Takes effect on next login / after reboot)"
echo ""

# --------------------------------------------------------------------------
# 3. Copy application files to install directory
# --------------------------------------------------------------------------
echo "[3/5] Copying application files to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
# Copy everything except this setup script and the service file itself
rsync -av --exclude='setup_pi.sh' \
          --exclude='get-pip.py' \
          --exclude='__pycache__' \
          --exclude='*.pyc' \
          . "$INSTALL_DIR/"
echo "      Done."
echo ""

# --------------------------------------------------------------------------
# 4. Install and enable the systemd service
# --------------------------------------------------------------------------
echo "[4/5] Installing systemd service ..."
sudo cp "$SERVICE_FILE" "$SYSTEMD_DIR/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service"
echo "      Service enabled — will start automatically at boot."
echo ""

# --------------------------------------------------------------------------
# 5. Start the service now
# --------------------------------------------------------------------------
echo "[5/5] Starting service now ..."
sudo systemctl start "${SERVICE_NAME}.service"
sleep 2   # give it a moment to come up

STATUS=$(systemctl is-active "${SERVICE_NAME}.service" 2>/dev/null || true)
if [ "$STATUS" = "active" ]; then
    echo "      Service is running."
else
    echo "      WARNING: Service status is '$STATUS'. Check logs below."
fi
echo ""

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
PI_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "======================================================"
echo " Setup Complete!"
echo "======================================================"
echo ""
echo "  Web interface : http://${PI_IP}:5000"
echo ""
echo "  Useful commands:"
echo "    Check status  : sudo systemctl status $SERVICE_NAME"
echo "    Live logs     : sudo journalctl -u $SERVICE_NAME -f"
echo "    Stop service  : sudo systemctl stop $SERVICE_NAME"
echo "    Restart       : sudo systemctl restart $SERVICE_NAME"
echo "    Disable boot  : sudo systemctl disable $SERVICE_NAME"
echo ""
echo "  NOTE: A reboot (or re-login) is needed before the dialout"
echo "  group change takes effect if the service couldn't open the"
echo "  serial port. Run:  sudo reboot"
echo ""
