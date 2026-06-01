# RK3568 Debian USB Bridge Version Notes

## Current Version

- Version: `v0.911.68`
- Target board: ATK-DLRK3568 / RK3568 Debian
- Repository: `clm1938243760/Debian-usb-bridge`
- Runtime path on board: `/opt/rk3568_gateway`
- Runtime state path: `/var/lib/rk3568-gateway`
- Python package version: `0.911.68`

## Version Scope

`v0.911.68`保存的是 RK3568 当前迁移版本。这个版本保留原来的扫码、API 查询、HID 录入、打印捕获、报告上传业务，并把 RK3588 上验证过的视觉流程迁移到 RK3568。

## History

- `v0.8`: RK3568 gateway stable baseline。
- `v0.810`: printer UDC guard 修改前版本。
- `v0.900`: RK3568 业务闭环版本。
- `v0.901`: v0.900 后续修正版本。
- `v0.902`: v0.901 后续修正版本。
- `v0.910`: 允许检查状态 30 和 40。
- `v0.911.68`: RK3568 加入视觉检测流程和 USB HDMI 采集卡截图流程。

## Main Functions

1. Original RK3568 business flow
   - 扫码枪输入体检号。
   - 查询体检系统接口。
   - 根据项目类型判断是否自动录入。
   - HID 键盘和鼠标自动录入人体成分检查信息。
   - 捕获 Windows 打印数据。
   - 监听报告目录并上传报告。
   - 上传成功后触发实体打印；上传失败不打印。

2. Vision flow added in this version
   - 使用 USB HDMI 采集卡读取 Windows 画面。
   - 当前稳定采集节点: `/dev/video9`。
   - 当前稳定采集格式: MJPG 1920x1080 30fps。
   - GStreamer 使用 `io-mode=2`，连续取 30 帧并保存第 29 帧，避免首帧黑屏或未稳定。
   - 视觉接口:
     - `POST /icon/locate`: 识别桌面“人体成分分析仪”图标并返回坐标。
     - `POST /window/detect`: 返回窗口 label、box 和 OCR 坐标。

3. Linear automation flow
   - 扫码并成功获取患者 API 信息后，才启动视觉流程。
   - 未检测到窗口时调用桌面图标识别并双击打开软件。
   - 检测到 `label0` 且存在“登录”时点击登录。
   - 检测到 `label1` 且存在“未选择患者”和“就绪”时点击“新建患者”。
   - 检测到 `label2` 时执行原 HID 表单录入。
   - 表单录入完成后，检测 `label1` 和“患者号”“就绪”，点击“开始检查”。
   - 检查完成后点击“数据分析”。
   - 检测到 `label4` 和“是否生成PDF报告？”时，在所有包含“是”的 OCR 字段中选择纵坐标更大的坐标点击。
   - 检测到 `label5` 和“检查报告已生成！”时点击“确定”。
   - 回到 `label1` 后点击“新建患者”，任务结束。

## RK3568 Capture Notes

- `/dev/video-camera0` 在当前板子上可能指向 RKISP 摄像头节点，不适合用于 HDMI 采集卡。
- 当前采集卡在 USB2.0 口可枚举为 `MACROSILICON USB Video`。
- USB3.0 口在当前板子上没有稳定枚举该采集卡。
- 推荐检查命令:

```bash
v4l2-ctl --list-devices
v4l2-ctl --list-formats-ext -d /dev/video9
lsusb -t
```

## Services

- Main service: `rk3568-gateway.service`
- Local API: `0.0.0.0:8080`
- Python module name is still `rk3588_gateway` for compatibility with the existing code layout.

## Validation Commands

```bash
systemctl status rk3568-gateway.service --no-pager
journalctl -u rk3568-gateway.service -f
curl http://127.0.0.1:8080/health
curl -sS -X POST http://127.0.0.1:8080/scan \
  -H "Content-Type: application/json" \
  -d '{"code":"P2605260007"}'
```

## Manual Vision Probe

```bash
mkdir -p /tmp/vision_probe
rm -f /tmp/vision_probe/.current_*.jpg

gst-launch-1.0 -q -e \
  v4l2src device=/dev/video9 io-mode=2 num-buffers=30 ! \
  'image/jpeg,width=1920,height=1080,framerate=30/1' ! \
  multifilesink location=/tmp/vision_probe/.current_%02d.jpg

cp /tmp/vision_probe/.current_29.jpg /tmp/vision_probe/current.jpg
```

