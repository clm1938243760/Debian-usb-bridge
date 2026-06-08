# RK3568 Debian USB Bridge Version Notes

## Current Version

- Version: `v0.917.68`
- Target board: ATK-DLRK3568 / RK3568 Debian
- Repository: `clm1938243760/Debian-usb-bridge`
- Runtime path on board: `/opt/rk3568_gateway`
- Runtime state path: `/var/lib/rk3568-gateway`
- Python package version: `0.917.68`

## Version Scope

`v0.917.68` saves the RK3568 BodyPass birthday input correction release. BodyPass now fills the API birthday into the right-side birth-date field, clears the default value with a double-click plus Delete before typing, and keeps the member ID/name input flow unchanged.

## History

- `v0.8`: RK3568 gateway stable baseline。
- `v0.810`: printer UDC guard 修改前版本。
- `v0.900`: RK3568 业务闭环版本。
- `v0.901`: v0.900 后续修正版本。
- `v0.902`: v0.901 后续修正版本。
- `v0.910`: 允许检查状态 30 和 40。
- `v0.911.68`: RK3568 加入视觉检测流程和 USB HDMI 采集卡截图流程。
- `v0.912.68`: RK3568 视觉流程定位版本，加入 PP-OCR/RKNN 常驻服务、窗口检测接口和 U 盘弹窗自动关闭逻辑。
- `v0.913.68`: 加入双软件 profile 切换和 BodyPass 自动流程，完成 BodyPass 打印后预览窗口、检测结果明细窗口固定坐标关闭。
- `v0.914.68`: 优化 BodyPass 板端视觉速度和稳定性，加入 ROI OCR 接口、阶段 ROI 轮询、模板优先图标定位、输入框主窗口相对坐标和更短等待参数。
- `v0.915.68`: Adds lightweight BodyPass main-window title ROI detection after icon open, uses a synthetic fixed main-window box for input and later stage ROIs, and validates the flow with 20 consecutive RK3568 board runs.
- `v0.916.68`: Adds the unified 480x320 full-screen UI, revised patient workflow states, lightweight already-open BodyPass detection, same-frame full-window fallback, and moved-window anchor recovery.
- `v0.917.68`: Adds BodyPass birthday input from the patient API and corrects the target coordinate to the right-side birth-date field instead of the phone-number field.

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
   - GStreamer 使用 `io-mode=2`，当前默认连续取 4 帧并选择可用 JPEG 帧，兼顾画面稳定和启动速度。
   - 视觉接口:
     - `POST /icon/locate`: 识别桌面“人体成分分析仪”图标并返回坐标。
     - `POST /window/detect`: 返回窗口 label、box 和 OCR 坐标。

3. Software profile switching
   - 配置入口: `/opt/rk3568_gateway/config.yaml`。
   - 切换字段: `active_profile`。
   - 当前内置 profile:
     - `body_composition`: 原“人体成分分析仪”流程。
     - `bodypass`: BodyPass 流程。
   - profile 文件:
     - `profiles/body_composition.yaml`
     - `profiles/bodypass.yaml`
   - profile 会覆盖 `device.type`、`vision.software`、`vision.flow` 和 profile 内配置的视觉参数。
   - 切换后只需要重启 `rk3568-gateway.service`，不需要重启 `rk3568-ppocr.service`。

4. BodyPass automation flow
   - 通过 `BodyPass` 图标模板定位并打开桌面软件。
   - 识别 `人体成分数据管理程序（BodyPass）` 主窗口后输入编号和姓名。
   - 对 OCR 不稳定的顶部工具栏使用主窗口相对坐标:
     - `传输会员信息`: 主窗口左上角偏移 `(820, 94)`。
     - `测量明细`: 主窗口左上角偏移 `(570, 94)`。
   - 等待 `Machine State = 显示检测结果` 后打开检测结果明细。
   - 点击 `预览检测结果`，在预览窗口中点击 `打印`。
   - 打印弹窗使用窗口相对坐标点击 `打印（P）`。
   - 打印完成后，预览窗口 OCR 可能只返回报告正文，因此预览关闭使用固定相对坐标 `(923, 78)`。
   - 返回检测结果明细后，明细关闭使用固定相对坐标 `(920, 190)`。

5. Linear automation flow
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

## Profile Switch Commands

切到原“人体成分分析仪”:

```bash
sudo sed -i 's/^active_profile:.*/active_profile: body_composition/' /opt/rk3568_gateway/config.yaml
sudo systemctl restart rk3568-gateway.service
```

切到 BodyPass:

```bash
sudo sed -i 's/^active_profile:.*/active_profile: bodypass/' /opt/rk3568_gateway/config.yaml
sudo systemctl restart rk3568-gateway.service
```

检查当前 profile:

```bash
grep '^active_profile:' /opt/rk3568_gateway/config.yaml
systemctl is-active rk3568-gateway.service
```

## v0.917.68 Changes

- BodyPass member input:
  - Adds API birthday input during the BodyPass member-info stage.
  - Uses `birthday` directly when present, and falls back to `nian`/`yue`/`ri` as `YYYY-MM-DD`.
  - Clears the default birth-date value by double-clicking the input field, pressing Delete, then typing the API value.
  - Corrects the birth-date coordinate to the right-side `出生日期` field at main-window-relative offset `(479, 224)`, absolute `(946, 390)` for the verified 1920x1080 BodyPass window.
  - Keeps member ID and member name input coordinates unchanged.
- HID input:
  - Adds a reusable double-click helper.
  - Adds a reusable clear-and-input helper for fields that already contain a default value.

## v0.917.68 Validation

- Local unit tests: `py -3.14 -m unittest discover -s tests -v`, `60 tests OK`.
- Local compile check: `py -3.14 -m compileall -q src scripts tests`.
- Source whitespace check: `git diff --check`.
- RK3568 deployment target: `linaro@192.168.20.250:/opt/rk3568_gateway`.
- RK3568 service check: `rk3568-gateway.service` active and `/health` returned `{"ok": true}`.

## v0.916.68 Changes

- RK3568 status UI:
  - All primary states render at the same 480x320 size without simulated black borders.
  - Startup shows `智能体已连接`, then transitions to patient check-in.
  - Workflow states cover waiting for check-in, item selection, patient-not-found, checking, automatic input, and completion.
  - The item selector supports four visible rows without overlap.
- BodyPass startup detection:
  - The software-already-open path checks the lightweight title ROI before attempting desktop icon detection.
  - The title search ROI was widened to tolerate normal BodyPass window movement.
  - Member ID/name label OCR derives a dynamic main-window anchor for relative-coordinate HID input.
  - An initial lightweight miss can fall back to full-window detection using the same captured frame.
  - Icon opening is skipped when the BodyPass main window is already detected.
- Automated coverage:
  - Added framebuffer render tests for state dimensions and non-black screen edges.
  - Added BodyPass tests for already-open detection, same-capture fallback, and moved-window anchoring.

## v0.916.68 Validation

- Local unit tests: `py -3.14 -m unittest discover -s tests -v`, `59 tests OK`.
- Local compile check: `py -3.14 -m compileall -q src scripts tests`.
- Source whitespace check: `git diff --check`.
- RK3568 deployment target: `linaro@192.168.20.250:/opt/rk3568_gateway`.

## v0.915.68 Changes

- BodyPass main-window detection:
  - After the BodyPass desktop icon is opened, main-window readiness now checks only the fixed title ROI instead of running another full-window OCR pass.
  - Recognized title fragments include `人体成分数据管理程序`, `体成分数据管理程序`, `BodyPas`, and `Body Pass`.
  - A successful lightweight title ROI check returns a synthetic main-window response with fixed box `(467, 166, 1479, 895)`.
  - Member ID/name input and later BodyPass stage ROIs continue to use main-window-relative coordinates.
  - Every third lightweight miss still falls back to full-window detection, preserving recovery behavior when the window moves or the title ROI is not visible.
- RK3568 board validation:
  - 20 consecutive BodyPass scan runs were completed on `linaro@192.168.20.250`.
  - Closed-start group: 10/10 successful, average scan-to-HID start `11.172s`, average full flow `45.017s`.
  - Open-start group: 10/10 successful, average scan-to-HID start `9.208s`, average full flow `44.291s`.
  - No lightweight main-window fallback was needed in the 10 closed-start runs.
  - One open-start run re-entered the icon-open path because the already-open main window was not detected, but the flow still completed successfully.

## v0.915.68 Validation

- Local unit tests: `py -3.14 -m unittest discover -s tests -v`, `54 tests OK`.
- Local compile check: `py -3.14 -m compileall -q src scripts tests`.
- RK3568 services after deploy:
  - `rk3568-gateway.service`: active.
  - `rk3568-ppocr.service`: active.
- Board stress result files:
  - `/tmp/bodypass_stress.log`
  - `/tmp/bodypass_stress_result.json`

## v0.914.68 Changes

- Capture and wait timing:
  - `capture_frames` reduced to `4` for faster UVC MJPG capture.
  - BodyPass `wait_after_open` reduced to `1.2` in the profile.
  - `wait_after_action`, `wait_after_no_detection`, and `analysis_wait` reduced for faster visual polling.
- Vision backend:
  - `/window/detect` accepts optional `roi_box`, `roi_margin`, and `roi_scale`.
  - ROI requests skip full YOLO window detection and run OCR only on the requested region.
  - `/icon/locate` uses the configured icon template first when available, avoiding unnecessary full OCR for BodyPass desktop icon detection.
- BodyPass flow:
  - Main window detection still establishes the BodyPass window box.
  - Later result/detail/preview/print stages use main-window-relative ROI OCR with periodic full-window fallback.
  - Member ID and member name input boxes now use main-window-relative coordinates instead of OCR matching the `编号` and `姓名` labels.
  - BodyPass stage polling interval reduced to `0.2s`.
- Measured RK3568 software-not-open path:
  - From scan workflow start to HID member ID input start: `15.864s`.
  - The BodyPass profile override now loads `wait_after_open = 1.2` on the board.
  - The current largest costs are full main-window detection, icon template location, and the post-double-click open wait.

## v0.914.68 Validation

- Local unit tests: `py -3.14 -m unittest discover -s tests -v`, `53 tests OK`.
- Local compile check: `py -3.14 -m compileall -q src scripts tests`.
- RK3568 deploy check:
  - `rk3568-gateway.service`: active.
  - `rk3568-ppocr.service`: active.
  - `curl http://127.0.0.1:8080/health`: OK.
  - `curl http://127.0.0.1:5002/health`: OK.
- BodyPass scan verification:
  - Test code: `P2605260007`.
  - Software-not-open path opened BodyPass, detected main window, started HID member ID input at main-window-relative coordinate `(685, 362)`.
  - Scan workflow start to HID member ID input start: `15.864s`.
  - Full BodyPass print flow reached preview close and detail close stages.

## v0.913.68 Validation

- Local unit tests: `py -3.14 -m unittest discover -s tests -v`，50 tests OK。
- Local compile check: `py -3.14 -m compileall -q src scripts tests`。
- RK3568 services after deploy:
  - `rk3568-gateway.service`: active。
  - `rk3568-ppocr.service`: active。
- BodyPass scan verification:
  - Test code: `P2605260007`。
  - Completed steps: input member id/name, transfer member info, wait result state, open measure detail, preview result, print, close preview, close detail.
  - Final screen returned to BodyPass main window.

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
