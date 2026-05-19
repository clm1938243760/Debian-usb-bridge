# RK3568 Headless Gateway

Debian 10 service for the ATK-DLRK3568 EVB1 board. It reads the USB scanner on
the RK3568 host port, exposes one USB composite gadget to the Windows host, runs
the automatic HID input workflow, receives reports from the virtual printer or
MSC disk, converts them to PDF, and submits the PDF to the local CUPS printer.

## Target Board

- Board: Rockchip RK3568 ATK EVB1 DDR4 V10
- Kernel: Debian 10, Rockchip 4.19.x
- UDC: `fcc00000.dwc3`
- Python: `3.7.3`
- SPI screen: kernel framebuffer `/dev/fb0`, `fb_ili9486`, `320x480`, `16bpp`
- UI orientation: horizontal, rendered as `480x320` and rotated into `/dev/fb0`
- Scanner: `/dev/input/by-id/usb-USBKey_Chip_USBKey_Module_202730041341-event-kbd`
- GPIO keys: `DOWN=GPIO4_B2/gpio138`, `OK=GPIO4_B3/gpio139`, active-low

## USB Gadget

The Windows host sees a single composite device:

- USB printer gadget: `/dev/g_printer0`
- USB mass storage gadget: `/var/lib/rk3568-gateway/msc/ums_shared.img`
- HID keyboard: `/dev/hidg0`
- HID mouse: `/dev/hidg1`

The setup script is:

```bash
sudo bash /opt/rk3568_gateway/scripts/setup_usb_composite_gadget.sh
```

## Install

Copy this project to the RK3568 board, then run from the project directory:

```bash
sudo bash install_debian.sh
sudo nano /opt/rk3568_gateway/config.yaml
sudo systemctl restart rk3568-usb-gadget rk3568-gateway rk3568-fb-status
```

Useful logs:

```bash
sudo journalctl -u rk3568-usb-gadget -n 100 --no-pager
sudo journalctl -u rk3568-gateway -f
sudo journalctl -u rk3568-fb-status -f
```

## Checks

```bash
ls /sys/class/udc
ls -l /dev/g_printer0 /dev/hidg0 /dev/hidg1
ls -l /dev/fb0
cat /sys/class/graphics/fb0/name
cat /sys/class/graphics/fb0/virtual_size
cat /sys/class/graphics/fb0/bits_per_pixel
ls -l /dev/input/by-id/
lpstat -p -d
lpinfo -v
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/display/state
```

## Report Handling

The scan/HID workflow and report receiving workflow are independent:

1. Scanner input queries the patient API, shows selectable exam items on the SPI UI, and sends HID keyboard/mouse actions to Windows.
2. A print job received from `/dev/g_printer0` is saved as raw data, converted from PostScript to PDF when possible, and printed through CUPS.
3. Files copied into the MSC disk are copied locally, converted to PDF when possible, and printed through CUPS.

PDFs from both paths are saved under:

```bash
/var/lib/rk3568-gateway/reports_pdf
```
