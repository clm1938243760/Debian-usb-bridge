#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/rk3568_gateway}"
DATA_DIR="${DATA_DIR:-/var/lib/rk3568-gateway}"
SERVICE_NAME="rk3568-gateway.service"
GADGET_SERVICE_NAME="rk3568-usb-gadget.service"
FB_STATUS_SERVICE_NAME="rk3568-fb-status.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run as root: sudo bash install_debian.sh"
  exit 1
fi

apt update
apt install -y \
  python3 python3-venv python3-pip python3-dev build-essential \
  cups cups-filters ghostscript printer-driver-hpcups hplip \
  libreoffice \
  rsync curl nano openssh-client sshpass dosfstools util-linux gpiod \
  fonts-wqy-microhei libjpeg-dev zlib1g-dev libfreetype6-dev

mkdir -p "$APP_DIR" "$DATA_DIR"
rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "config.yaml" \
  ./ "$APP_DIR/"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade "pip<24" "setuptools<68" wheel
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

if [[ ! -s "$APP_DIR/config.yaml" ]]; then
  cp "$APP_DIR/config.example.yaml" "$APP_DIR/config.yaml"
fi

chmod +x "$APP_DIR/scripts/setup_usb_composite_gadget.sh"
chmod +x "$APP_DIR/scripts/setup_usb_printer_gadget.sh" 2>/dev/null || true
chmod +x "$APP_DIR/scripts/list_gpio_lines.sh"
chmod +x "$APP_DIR/scripts/configure_gpio_buttons.py"
chmod +x "$APP_DIR/scripts/fb_status.py"

cp "$APP_DIR/systemd/$GADGET_SERVICE_NAME" "/etc/systemd/system/$GADGET_SERVICE_NAME"
cp "$APP_DIR/systemd/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
cp "$APP_DIR/systemd/$FB_STATUS_SERVICE_NAME" "/etc/systemd/system/$FB_STATUS_SERVICE_NAME"

systemctl daemon-reload
systemctl disable --now rk3588-gateway.service 2>/dev/null || true
systemctl disable --now rk3588-usb-printer-gadget.service 2>/dev/null || true
systemctl disable --now rk3588-usb-hid-gadget.service 2>/dev/null || true
systemctl disable --now rk3588-fb-status.service 2>/dev/null || true

systemctl enable "$GADGET_SERVICE_NAME" "$SERVICE_NAME" "$FB_STATUS_SERVICE_NAME"
systemctl restart cups || true
systemctl restart "$GADGET_SERVICE_NAME" || true
systemctl restart "$SERVICE_NAME"
systemctl restart "$FB_STATUS_SERVICE_NAME" || true

echo "Installed to $APP_DIR"
echo "Data directory: $DATA_DIR"
echo "Edit $APP_DIR/config.yaml for API/printer details, then run:"
echo "  sudo systemctl restart $GADGET_SERVICE_NAME $SERVICE_NAME $FB_STATUS_SERVICE_NAME"
echo "  sudo journalctl -u $SERVICE_NAME -f"
