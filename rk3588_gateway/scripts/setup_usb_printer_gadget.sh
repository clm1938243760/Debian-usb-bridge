#!/usr/bin/env bash
set -euo pipefail

G="${GADGET_DIR:-/sys/kernel/config/usb_gadget/rockchip}"
CONFIG="$G/configs/b.1"
FUNCTIONS="$G/functions"
MSC_IMAGE="${MSC_IMAGE:-/var/lib/rk3588-gateway/msc/ums_shared.img}"
MSC_SIZE_MB="${MSC_SIZE_MB:-64}"
MSC_LABEL="${MSC_LABEL:-RK3588MSC}"

modprobe libcomposite 2>/dev/null || true
modprobe usb_f_mass_storage 2>/dev/null || true
modprobe usb_f_printer 2>/dev/null || true
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config

UDC="${UDC:-$(ls /sys/class/udc 2>/dev/null | head -n 1 || true)}"
if [[ -z "$UDC" ]]; then
  echo "No UDC found under /sys/class/udc"
  exit 1
fi

mkdir -p "$G"
mkdir -p "$(dirname "$MSC_IMAGE")"

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
echo "RK3588BRIDGE001" > "$G/strings/0x409/serialnumber"
echo "RK3588" > "$G/strings/0x409/manufacturer"
echo "RK3588 Printer MSC Bridge" > "$G/strings/0x409/product"

mkdir -p "$CONFIG/strings/0x409"
echo "USB Printer + Mass Storage" > "$CONFIG/strings/0x409/configuration"
echo 120 > "$CONFIG/MaxPower"

mkdir -p "$FUNCTIONS/printer.usb0"
echo 10 > "$FUNCTIONS/printer.usb0/q_len"
echo "MFG:RK3588;MDL:Virtual Printer;DES:RK3588 Virtual Printer;CMD:RAW;CLS:PRINTER;" > "$FUNCTIONS/printer.usb0/pnp_string"

rm -f "$CONFIG/f_printer" "$CONFIG/f_mass_storage" "$CONFIG/f1" "$CONFIG/f2" "$CONFIG/f3" 2>/dev/null || true
ln -s "$FUNCTIONS/printer.usb0" "$CONFIG/f1"

if [[ -e "$FUNCTIONS/mass_storage.usb0/lun.0/file" ]]; then
  echo "" > "$FUNCTIONS/mass_storage.usb0/lun.0/file" 2>/dev/null || true
fi
MSC_FUNCTION="$FUNCTIONS/mass_storage.0"
mkdir -p "$MSC_FUNCTION"
echo "" > "$MSC_FUNCTION/lun.0/file" 2>/dev/null || true
echo 0 > "$MSC_FUNCTION/stall" 2>/dev/null || true
echo 0 > "$MSC_FUNCTION/lun.0/cdrom"
echo 0 > "$MSC_FUNCTION/lun.0/ro"
echo 1 > "$MSC_FUNCTION/lun.0/removable"
echo "$MSC_IMAGE" > "$MSC_FUNCTION/lun.0/file"

ln -s "$MSC_FUNCTION" "$CONFIG/f2"

echo "$UDC" > "$G/UDC"
sleep 1

ls -l /dev/g_printer0
echo "mass storage backing file:"
cat "$MSC_FUNCTION/lun.0/file"
echo "config links:"
ls -l "$CONFIG"
echo "USB printer + MSC gadget enabled on UDC=$UDC image=$MSC_IMAGE"
