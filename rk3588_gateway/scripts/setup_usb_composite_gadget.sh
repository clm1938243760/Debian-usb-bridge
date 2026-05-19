#!/usr/bin/env bash
set -euo pipefail

G="${GADGET_DIR:-/sys/kernel/config/usb_gadget/rockchip}"
CONFIG="$G/configs/b.1"
FUNCTIONS="$G/functions"
MSC_IMAGE="${MSC_IMAGE:-/var/lib/rk3568-gateway/msc/ums_shared.img}"
MSC_SIZE_MB="${MSC_SIZE_MB:-64}"
MSC_LABEL="${MSC_LABEL:-RK3568MSC}"
UDC="${UDC:-fcc00000.dwc3}"

modprobe libcomposite 2>/dev/null || true
modprobe usb_f_hid 2>/dev/null || true
modprobe usb_f_printer 2>/dev/null || true
modprobe usb_f_mass_storage 2>/dev/null || true
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config

if [[ -z "$UDC" || ! -e "/sys/class/udc/$UDC" ]]; then
  UDC="$(ls /sys/class/udc 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "$UDC" ]]; then
  echo "No UDC found under /sys/class/udc"
  exit 1
fi

mkdir -p "$G" "$(dirname "$MSC_IMAGE")"

if [[ ! -s "$MSC_IMAGE" ]]; then
  dd if=/dev/zero of="$MSC_IMAGE" bs=1M count="$MSC_SIZE_MB"
  if command -v mkfs.vfat >/dev/null 2>&1; then
    mkfs.vfat -n "$MSC_LABEL" "$MSC_IMAGE"
  elif command -v mkfs.fat >/dev/null 2>&1; then
    mkfs.fat -n "$MSC_LABEL" "$MSC_IMAGE"
  else
    echo "mkfs.vfat not found. Install dosfstools first."
    exit 1
  fi
fi

if [[ -f "$G/UDC" ]]; then
  echo "" > "$G/UDC" 2>/dev/null || true
  sleep 1
fi

if [[ -d "$G/configs" ]]; then
  find "$G/configs" -maxdepth 2 -type l -exec rm -f {} \; 2>/dev/null || true
fi

echo 0x2207 > "$G/idVendor"
echo 0x3568 > "$G/idProduct"
echo 0x0200 > "$G/bcdUSB"
echo 0x0100 > "$G/bcdDevice"
echo 0x00 > "$G/bDeviceClass"
echo 0x00 > "$G/bDeviceSubClass"
echo 0x00 > "$G/bDeviceProtocol"

mkdir -p "$G/strings/0x409"
echo "RK3568BRIDGE001" > "$G/strings/0x409/serialnumber"
echo "RK3568" > "$G/strings/0x409/manufacturer"
echo "RK3568 HID Printer MSC Bridge" > "$G/strings/0x409/product"

mkdir -p "$CONFIG/strings/0x409"
echo "Printer + MSC + HID Keyboard + HID Mouse" > "$CONFIG/strings/0x409/configuration"
echo 120 > "$CONFIG/MaxPower"

mkdir -p "$FUNCTIONS/printer.usb0"
echo 10 > "$FUNCTIONS/printer.usb0/q_len"
echo "MFG:RK3568;MDL:Virtual Printer;DES:RK3568 Virtual Printer;CMD:POSTSCRIPT,RAW;CLS:PRINTER;" > "$FUNCTIONS/printer.usb0/pnp_string"

MSC_FUNCTION="$FUNCTIONS/mass_storage.0"
mkdir -p "$MSC_FUNCTION"
echo "" > "$MSC_FUNCTION/lun.0/file" 2>/dev/null || true
echo 0 > "$MSC_FUNCTION/stall" 2>/dev/null || true
echo 0 > "$MSC_FUNCTION/lun.0/cdrom"
echo 0 > "$MSC_FUNCTION/lun.0/ro"
echo 1 > "$MSC_FUNCTION/lun.0/removable"
echo "$MSC_IMAGE" > "$MSC_FUNCTION/lun.0/file"

mkdir -p "$FUNCTIONS/hid.usb0"
echo 1 > "$FUNCTIONS/hid.usb0/protocol"
echo 1 > "$FUNCTIONS/hid.usb0/subclass"
echo 8 > "$FUNCTIONS/hid.usb0/report_length"
printf '\x05\x01\x09\x06\xa1\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0' > "$FUNCTIONS/hid.usb0/report_desc"

mkdir -p "$FUNCTIONS/hid.usb1"
echo 2 > "$FUNCTIONS/hid.usb1/protocol"
echo 1 > "$FUNCTIONS/hid.usb1/subclass"
echo 5 > "$FUNCTIONS/hid.usb1/report_length"
printf '\x05\x01\x09\x02\xa1\x01\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x03\x15\x00\x25\x01\x95\x03\x75\x01\x81\x02\x95\x01\x75\x05\x81\x03\x05\x01\x09\x30\x09\x31\x16\x00\x00\x26\xff\x7f\x36\x00\x00\x46\xff\x7f\x75\x10\x95\x02\x81\x02\xc0\xc0' > "$FUNCTIONS/hid.usb1/report_desc"

rm -f "$CONFIG/f_printer" "$CONFIG/f_mass_storage" "$CONFIG/f_keyboard" "$CONFIG/f_mouse" "$CONFIG/f1" "$CONFIG/f2" "$CONFIG/f3" "$CONFIG/f4" 2>/dev/null || true
ln -s "$FUNCTIONS/printer.usb0" "$CONFIG/f1"
ln -s "$MSC_FUNCTION" "$CONFIG/f2"
ln -s "$FUNCTIONS/hid.usb0" "$CONFIG/f3"
ln -s "$FUNCTIONS/hid.usb1" "$CONFIG/f4"

echo "$UDC" > "$G/UDC"
sleep 1

ls -l /dev/g_printer0 /dev/hidg0 /dev/hidg1 2>/dev/null || true
echo "mass storage backing file:"
cat "$MSC_FUNCTION/lun.0/file"
echo "USB composite gadget enabled on UDC=$UDC image=$MSC_IMAGE"
