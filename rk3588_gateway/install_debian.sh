#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/rk3588_gateway}"
SERVICE_NAME="rk3588-gateway.service"
GADGET_SERVICE_NAME="rk3588-usb-printer-gadget.service"
DISPLAY_SERVICE_NAME="rk3588-display-kiosk.service"
FB_STATUS_SERVICE_NAME="rk3588-fb-status.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run as root: sudo bash install_debian.sh"
  exit 1
fi

apt update
apt install -y python3 python3-venv python3-pip python3-dev build-essential cups rsync curl nano openssh-client sshpass dosfstools util-linux gpiod python3-pil fonts-wqy-microhei

mkdir -p "$APP_DIR" /var/lib/rk3588-gateway
rsync -a --delete \
  --exclude ".venv" \
  --exclude "config.yaml" \
  ./ "$APP_DIR/"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

if [[ ! -s "$APP_DIR/config.yaml" ]]; then
  cp "$APP_DIR/config.example.yaml" "$APP_DIR/config.yaml"
elif ! grep -q '^msc:' "$APP_DIR/config.yaml"; then
  cat >> "$APP_DIR/config.yaml" <<'EOF'

msc:
  enabled: true
  image_path: "/var/lib/rk3588-gateway/msc/ums_shared.img"
  mount_dir: "/mnt/rk3588-gateway-msc"
  output_dir: "/var/lib/rk3588-gateway/msc_files"
  state_dir: "/var/lib/rk3588-gateway/msc_state"
  gadget_dir: "/sys/kernel/config/usb_gadget/rockchip"
  udc_device: ""
  poll_interval_seconds: 5
  stable_seconds: 3
  quiet_seconds: 2
  init_baseline: true
  rebuild_command: "/opt/rk3588_gateway/scripts/setup_usb_printer_gadget.sh"
  copy_recursive: true
  ignore_names: ["System Volume Information", "$RECYCLE.BIN"]
EOF
fi

if ! grep -q '^gpio:' "$APP_DIR/config.yaml"; then
  cat >> "$APP_DIR/config.yaml" <<'EOF'

gpio:
  enabled: false
  consumer: "rk3588-gateway"
  lines:
    - name: "gpio1"
      enabled: false
      chip: "/dev/gpiochip0"
      line: 0
      direction: "out"
      active_low: false
      default: 0
    - name: "gpio2"
      enabled: false
      chip: "/dev/gpiochip0"
      line: 1
      direction: "out"
      active_low: false
      default: 0
    - name: "gpio3"
      enabled: false
      chip: "/dev/gpiochip0"
      line: 2
      direction: "in"
      active_low: false
      default: 0
    - name: "gpio4"
      enabled: false
      chip: "/dev/gpiochip0"
      line: 3
      direction: "in"
      active_low: false
      default: 0
EOF
fi

if ! grep -q '^report_pdf:' "$APP_DIR/config.yaml"; then
  cat >> "$APP_DIR/config.yaml" <<'EOF'

report_pdf:
  enabled: true
  output_dir: "/var/lib/rk3588-gateway/reports_pdf"
  keep_original: true
EOF
fi

"$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/configure_gpio_buttons.py" "$APP_DIR/config.yaml"

if grep -q '^scanner:' "$APP_DIR/config.yaml"; then
  sed -i '0,/^[[:space:]]*min_length:[[:space:]]*/s//  min_length: 8 # /' "$APP_DIR/config.yaml"
  sed -i 's/  min_length: 8 # .*/  min_length: 8/' "$APP_DIR/config.yaml"
fi

if grep -q '^msc:' "$APP_DIR/config.yaml"; then
  sed -i 's/^[[:space:]]*stable_seconds:[[:space:]]*.*/  stable_seconds: 3/' "$APP_DIR/config.yaml"
  sed -i 's/^[[:space:]]*quiet_seconds:[[:space:]]*.*/  quiet_seconds: 2/' "$APP_DIR/config.yaml"
  sed -i 's#^[[:space:]]*rebuild_command:[[:space:]].*#  rebuild_command: "/opt/rk3588_gateway/scripts/setup_usb_printer_gadget.sh"#' "$APP_DIR/config.yaml"
fi

if grep -q '^hid_input:' "$APP_DIR/config.yaml"; then
  sed -i 's/^[[:space:]]*action_delay_ms:[[:space:]]*.*/  action_delay_ms: 60/' "$APP_DIR/config.yaml"
  sed -i 's/^[[:space:]]*start_delay_ms:[[:space:]]*.*/  start_delay_ms: 150/' "$APP_DIR/config.yaml"
  sed -i 's/^[[:space:]]*powershell_wait_ms:[[:space:]]*.*/  powershell_wait_ms: 1800/' "$APP_DIR/config.yaml"
fi

chmod +x "$APP_DIR/scripts/setup_usb_printer_gadget.sh"
chmod +x "$APP_DIR/scripts/list_gpio_lines.sh"
chmod +x "$APP_DIR/scripts/configure_gpio_buttons.py"
chmod +x "$APP_DIR/scripts/start_display_kiosk.sh"
chmod +x "$APP_DIR/scripts/fb_status.py"
cp "$APP_DIR/systemd/$GADGET_SERVICE_NAME" "/etc/systemd/system/$GADGET_SERVICE_NAME"
cp "$APP_DIR/systemd/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
cp "$APP_DIR/systemd/$DISPLAY_SERVICE_NAME" "/etc/systemd/system/$DISPLAY_SERVICE_NAME"
cp "$APP_DIR/systemd/$FB_STATUS_SERVICE_NAME" "/etc/systemd/system/$FB_STATUS_SERVICE_NAME"
systemctl daemon-reload
systemctl disable --now rk3588-usb-hid-gadget.service 2>/dev/null || true
systemctl disable --now "$DISPLAY_SERVICE_NAME" 2>/dev/null || true
systemctl enable "$GADGET_SERVICE_NAME" "$SERVICE_NAME" "$FB_STATUS_SERVICE_NAME"
systemctl restart "$GADGET_SERVICE_NAME" || true
systemctl restart "$SERVICE_NAME"
systemctl restart "$FB_STATUS_SERVICE_NAME" || true

echo "Installed to $APP_DIR"
echo "Edit $APP_DIR/config.yaml, then run:"
echo "  sudo systemctl restart $GADGET_SERVICE_NAME $SERVICE_NAME"
echo "  sudo journalctl -u $SERVICE_NAME -f"
