# RK3588 Headless Gateway

Debian service for LubanCat/RK3588 headless deployments. It collects HID/barcode
scanner input, queues jobs locally, captures USB printer jobs, exposes a small
USB MSC disk, and can forward events to an upstream API.

## Layout

- `config.example.yaml` - copy to `config.yaml` and edit for your board.
- `requirements.txt` - Python dependencies for Debian.
- `src/rk3588_gateway/main.py` - service entrypoint.
- `src/rk3588_gateway/config.py` - typed configuration loader.
- `src/rk3588_gateway/hid.py` - HID/scanner event reader.
- `src/rk3588_gateway/printer.py` - CUPS/lp based print adapter.
- `src/rk3588_gateway/print_capture.py` - USB printer gadget capture.
- `src/rk3588_gateway/msc_monitor.py` - USB MSC image monitor and local copy.
- `src/rk3588_gateway/queue.py` - SQLite retry queue.
- `src/rk3588_gateway/uploader.py` - API relay client.
- `src/rk3588_gateway/api.py` - local control/status API.
- `systemd/rk3588-gateway.service` - systemd unit template.

## Debian Setup

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip cups
cd /opt/rk3588_gateway
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Edit `config.yaml`, then run:

```bash
. .venv/bin/activate
python -m rk3588_gateway.main --config config.yaml
```

Install as a service:

```bash
sudo cp systemd/rk3588-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rk3588-gateway
sudo journalctl -u rk3588-gateway -f
```

Or run the installer from the project directory on the board:

```bash
sudo bash install_debian.sh
sudo nano /opt/rk3588_gateway/config.yaml
sudo systemctl restart rk3588-gateway
```

## Useful Checks

Find scanner/HID event devices:

```bash
lsusb
ls -l /dev/input/by-id/
sudo evtest
```

Find printers:

```bash
lpstat -p -d
lp -d PRINTER_NAME test.txt
```

Check local API:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/status
```

Initialize USB printer + MSC gadget:

```bash
sudo bash scripts/setup_usb_printer_gadget.sh
ls -l /dev/g_printer0
ls -l /var/lib/rk3588-gateway/msc/ums_shared.img
```

Install gadget systemd unit:

```bash
sudo cp systemd/rk3588-usb-printer-gadget.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rk3588-usb-printer-gadget
sudo systemctl restart rk3588-gateway
```

Use CH9350 UART keyboard output:

```yaml
hid_input:
  keyboard_backend: "ch9350"
  mouse_backend: "none"
  ch9350_serial_device: "/dev/ttyS1"
  ch9350_baudrate: 115200
  ch9350_set_state2: true
```

Quick CH9350 UART check:

```bash
stty -F /dev/ttyS1 115200 cs8 -cstopb -parenb raw -echo
python3 scripts/test_ch9350_key.py
```

MSC files copied from the Windows-visible U disk are saved locally:

```bash
ls -l /var/lib/rk3588-gateway/msc_files
journalctl -u rk3588-gateway -f
```

List GPIO chips and lines:

```bash
sudo /opt/rk3588_gateway/scripts/list_gpio_lines.sh
```

GPIO local API:

```bash
curl http://127.0.0.1:8080/gpio
curl http://127.0.0.1:8080/gpio/gpio1
curl -X POST http://127.0.0.1:8080/gpio/gpio1 -H "Content-Type: application/json" -d '{"value":1}'
curl -X POST http://127.0.0.1:8080/gpio/gpio1/pulse -H "Content-Type: application/json" -d '{"value":1,"duration_ms":200}'
```

HDMI 480x320 status display:

```bash
curl http://127.0.0.1:8080/display/state
```

Open `http://127.0.0.1:8080/display` in the board browser. The page is designed
for a 480x320 window. If Chromium is installed and a desktop session is running
on HDMI:

```bash
/opt/rk3588_gateway/scripts/start_display_kiosk.sh
```

Optional autostart for graphical desktop:

```bash
systemctl enable --now rk3588-display-kiosk.service
```

Framebuffer status display without desktop:

```bash
systemctl enable --now rk3588-fb-status.service
systemctl status rk3588-fb-status --no-pager -l
```

Manual test:

```bash
/opt/rk3588_gateway/.venv/bin/python /opt/rk3588_gateway/scripts/fb_status.py --fb /dev/fb0 --width 480 --height 320
```

Create a manual queued event:

```bash
curl -X POST http://127.0.0.1:8080/events \
  -H "Content-Type: application/json" \
  -d '{"type":"manual.test","payload":{"code":"123456"}}'
```

Print text through the local API:

```bash
curl -X POST http://127.0.0.1:8080/print \
  -H "Content-Type: application/json" \
  -d '{"title":"test","text":"hello rk3588"}'
```
