import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def load_module():
    script = SRC / "rk3588_gateway" / "vision_flow.py"
    spec = importlib.util.spec_from_file_location("rk3588_gateway.vision_flow", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeHidOutput:
    def __init__(self):
        self.clicks = []
        self.forms = []
        self.inputs = []
        self.cleared_inputs = []

    async def click(self, x, y):
        self.clicks.append((x, y))

    async def double_click(self, x, y):
        self.clicks.append((x, y, "double"))

    async def input_text(self, text, x, y, field=""):
        self.inputs.append((text, x, y, field))

    async def clear_and_input_text(self, text, x, y, field=""):
        self.cleared_inputs.append((text, x, y, field))

    async def execute_form(self, task):
        self.forms.append(task)


class FakeVisionFlow:
    def __init__(self, responses, open_results=None, flow="body_composition"):
        vision = load_module()
        self._impl = vision.VisionFlow(
            SimpleNamespace(
                enabled=True,
                flow=flow,
                device="/dev/video9",
                workdir="/tmp/test-vision",
                icon_endpoint="http://127.0.0.1/icon",
                window_endpoint="http://127.0.0.1/window",
                software="人体成分分析仪",
                wait_after_open=0.0,
                wait_after_action=0.0,
                wait_after_no_detection=5.0,
                wait_after_start=0.0,
                analysis_wait=0.0,
                max_runtime=5.0,
                timeout_seconds=1.0,
                close_msc_popup_after_report=False,
                close_msc_popup_when_detected=True,
            ),
            FakeHidOutput(),
        )
        self.responses = list(responses)
        self.open_results = list(open_results or [])
        self.open_count = 0
        self.light_count = 0
        self.clicked_texts = []
        self.sleeps = []
        self._impl.detect_window = self.detect_window
        self._impl.detect_bodypass_main_window_light = self.detect_bodypass_main_window_light
        self._impl.detect_bodypass_stage_window = self.detect_bodypass_stage_window
        self._impl.open_app = self.open_app
        self._impl.click_ocr_text = self.click_ocr_text
        self._impl.sleep = self.sleep

    @property
    def hid_output(self):
        return self._impl.hid_output

    async def run_until_form_done(self, task):
        return await self._impl.run_until_form_done(task)

    async def detect_window(self, image_name):
        if not self.responses:
            raise AssertionError("unexpected detect_window call")
        return self.responses.pop(0)

    async def detect_bodypass_stage_window(self, stage, image_name, predicate):
        return await self.detect_window(image_name)

    async def detect_bodypass_main_window_light(self, image_name, full_fallback=False):
        self.light_count += 1
        return await self.detect_window(image_name)

    async def open_app(self, image_name):
        self.open_count += 1
        if self.open_results:
            return self.open_results.pop(0)
        return True

    async def click_ocr_text(self, response, text):
        self.clicked_texts.append(text)
        return True

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        return None


class VisionFlowTest(unittest.TestCase):
    def test_build_capture_command_uses_uvc_mjpg_stable_frame(self):
        vision = load_module()

        cmd = vision.build_capture_command(
            "/dev/video9",
            Path("/tmp/vision/window_1.jpg"),
            width=1920,
            height=1080,
            framerate=30,
            frames=30,
            io_mode=2,
            capture_format="mjpg",
        )

        self.assertIn("device=/dev/video9", cmd)
        self.assertIn("io-mode=2", cmd)
        self.assertIn("num-buffers=30", cmd)
        self.assertIn("image/jpeg,width=1920,height=1080,framerate=30/1", cmd)
        self.assertIn("multifilesink", cmd)
        self.assertIn("location=/tmp/vision/.window_1_%02d.jpg", cmd)

    def test_select_capture_frame_prefers_largest_frame_over_last_frame(self):
        vision = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "shot.jpg"
            (Path(temp_dir) / ".shot_00.jpg").write_bytes(b"\xff\xd8" + b"0" * 41654 + b"\xff\xd9")
            (Path(temp_dir) / ".shot_28.jpg").write_bytes(b"\xff\xd8" + b"1" * 187784 + b"\xff\xd9")
            (Path(temp_dir) / ".shot_29.jpg").write_bytes(b"\xff\xd8" + b"2" * 41105 + b"\xff\xd9")

            selected = vision.select_capture_frame(output, frames=30)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.name, ".shot_28.jpg")

    def test_select_capture_frame_ignores_large_non_jpeg_frame(self):
        vision = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "shot.jpg"
            (Path(temp_dir) / ".shot_28.jpg").write_bytes(b"\xff\xd8" + b"1" * 187784 + b"\xff\xd9")
            (Path(temp_dir) / ".shot_29.jpg").write_bytes(b"x" * 190363)

            selected = vision.select_capture_frame(output, frames=30)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.name, ".shot_28.jpg")

    def test_capture_jpeg_retries_when_batch_is_only_tiny_black_frames(self):
        vision = load_module()
        calls = []
        original_run = vision.subprocess.run

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "shot.jpg"

            def fake_run(cmd, check, timeout):
                calls.append((cmd, check, timeout))
                size = 41109 if len(calls) == 1 else 187788
                (Path(temp_dir) / ".shot_29.jpg").write_bytes(b"\xff\xd8" + b"x" * (size - 4) + b"\xff\xd9")

            try:
                vision.subprocess.run = fake_run
                vision.capture_jpeg(
                    "/dev/video9",
                    output,
                    timeout=1.0,
                    frames=30,
                    retry_delay=0.0,
                )
            finally:
                vision.subprocess.run = original_run

            self.assertEqual(len(calls), 2)
            self.assertEqual(output.stat().st_size, 187788)

    def test_bodypass_stage_roi_uses_main_window_anchor(self):
        vision = load_module()
        flow = vision.VisionFlow(
            SimpleNamespace(
                enabled=True,
                device="/dev/video9",
                workdir="/tmp/test-vision",
                icon_endpoint="http://127.0.0.1/icon",
                window_endpoint="http://127.0.0.1/window",
                software="BodyPass",
                capture_width=1920,
                capture_height=1080,
                capture_framerate=30,
                capture_frames=6,
                capture_io_mode=2,
                capture_format="mjpg",
                timeout_seconds=1.0,
                max_runtime=5.0,
            ),
            FakeHidOutput(),
        )
        flow.bodypass_main_box = (467, 166, 1479, 895)

        self.assertEqual(flow.bodypass_stage_roi("bodypass_result_state"), (467, 546, 807, 636))

    def test_bodypass_main_light_uses_title_roi_and_synthetic_window_box(self):
        vision = load_module()
        original_capture_jpeg = vision.capture_jpeg
        original_post_image = vision.post_image
        post_extras = []

        def fake_capture_jpeg(*args, **kwargs):
            return None

        def fake_post_image(endpoint, image_path, timeout, extra=None):
            post_extras.append(extra)
            if extra["roi_box"] == [430, 70, 950, 240]:
                return {
                    "ocr": [{"text": "体成分数据管理程序（BodyPas", "center": [588, 187]}],
                    "image_size": {"width": 1920, "height": 1080},
                    "elapsed_ms": 250,
                }
            return {
                "ocr": [
                    {"text": vision.BODYPASS_MEMBER_ID_TEXT, "center": [505, 362]},
                    {"text": vision.BODYPASS_MEMBER_NAME_TEXT, "center": [505, 390]},
                ],
                "image_size": {"width": 1920, "height": 1080},
                "elapsed_ms": 200,
            }

        flow = vision.VisionFlow(
            SimpleNamespace(
                enabled=True,
                device="/dev/video9",
                workdir="/tmp/test-vision",
                icon_endpoint="http://127.0.0.1/icon",
                window_endpoint="http://127.0.0.1/window",
                software="BodyPass",
                capture_width=1920,
                capture_height=1080,
                capture_framerate=30,
                capture_frames=4,
                capture_io_mode=2,
                capture_format="mjpg",
                timeout_seconds=1.0,
                max_runtime=5.0,
            ),
            FakeHidOutput(),
        )

        try:
            vision.capture_jpeg = fake_capture_jpeg
            vision.post_image = fake_post_image
            response = asyncio.run(flow.detect_bodypass_main_window_light("main.jpg"))
        finally:
            vision.capture_jpeg = original_capture_jpeg
            vision.post_image = original_post_image

        self.assertEqual(len(post_extras), 2)
        self.assertEqual(post_extras[0]["roi_box"], [430, 70, 950, 240])
        self.assertEqual(post_extras[1]["roi_box"], [450, 270, 720, 420])
        self.assertTrue(vision.is_bodypass_main_window(response))
        self.assertEqual(vision.bodypass_window_box(response), vision.BODYPASS_MAIN_WINDOW_BOX)
        self.assertTrue(response["bodypass_light"])

    def test_bodypass_main_box_tracks_moved_window_from_member_labels(self):
        vision = load_module()
        response = {
            "image_size": {"width": 1920, "height": 1080},
            "ocr": [
                {"text": vision.BODYPASS_MEMBER_ID_TEXT, "center": [638, 289]},
                {"text": vision.BODYPASS_MEMBER_NAME_TEXT, "center": [639, 316]},
            ],
        }

        self.assertEqual(
            vision.bodypass_main_box_from_member_labels(response),
            (600, 93, 1612, 822),
        )

    def test_bodypass_initial_light_miss_falls_back_on_the_same_capture(self):
        vision = load_module()
        original_capture_jpeg = vision.capture_jpeg
        original_post_image = vision.post_image
        capture_calls = []
        post_extras = []

        def fake_capture_jpeg(*args, **kwargs):
            capture_calls.append((args, kwargs))

        def fake_post_image(endpoint, image_path, timeout, extra=None):
            post_extras.append(extra)
            if extra is not None:
                return {"ocr": [], "elapsed_ms": 5}
            return {"ocr": [{"text": "desktop", "center": [1, 1]}], "elapsed_ms": 50}

        flow = vision.VisionFlow(
            SimpleNamespace(
                enabled=True,
                device="/dev/video9",
                workdir="/tmp/test-vision",
                icon_endpoint="http://127.0.0.1/icon",
                window_endpoint="http://127.0.0.1/window",
                software="BodyPass",
                capture_width=1920,
                capture_height=1080,
                capture_framerate=30,
                capture_frames=4,
                capture_io_mode=2,
                capture_format="mjpg",
                timeout_seconds=1.0,
                max_runtime=5.0,
            ),
            FakeHidOutput(),
        )

        try:
            vision.capture_jpeg = fake_capture_jpeg
            vision.post_image = fake_post_image
            response = asyncio.run(
                flow.detect_bodypass_main_window_light(
                    "main.jpg",
                    full_fallback=True,
                )
            )
        finally:
            vision.capture_jpeg = original_capture_jpeg
            vision.post_image = original_post_image

        self.assertEqual(len(capture_calls), 1)
        self.assertEqual(len(post_extras), 2)
        self.assertIsNotNone(post_extras[0])
        self.assertIsNone(post_extras[1])
        self.assertEqual(response["ocr"][0]["text"], "desktop")

    def test_bodypass_roi_falls_back_to_full_window_every_third_miss(self):
        vision = load_module()
        original_capture_jpeg = vision.capture_jpeg
        original_post_image = vision.post_image
        post_extras = []

        def fake_capture_jpeg(*args, **kwargs):
            return None

        def fake_post_image(endpoint, image_path, timeout, extra=None):
            post_extras.append(extra)
            if extra is not None:
                return {"ocr": [], "elapsed_ms": 5}
            return {"ocr": [{"text": "fallback", "center": [1, 1]}], "elapsed_ms": 50}

        flow = vision.VisionFlow(
            SimpleNamespace(
                enabled=True,
                device="/dev/video9",
                workdir="/tmp/test-vision",
                icon_endpoint="http://127.0.0.1/icon",
                window_endpoint="http://127.0.0.1/window",
                software="BodyPass",
                capture_width=1920,
                capture_height=1080,
                capture_framerate=30,
                capture_frames=6,
                capture_io_mode=2,
                capture_format="mjpg",
                timeout_seconds=1.0,
                max_runtime=5.0,
            ),
            FakeHidOutput(),
        )
        flow.bodypass_main_box = (467, 166, 1479, 895)

        try:
            vision.capture_jpeg = fake_capture_jpeg
            vision.post_image = fake_post_image
            for _ in range(3):
                response = asyncio.run(
                    flow.detect_bodypass_stage_window(
                        "bodypass_result_state",
                        "stage.jpg",
                        lambda item: False,
                    )
                )
        finally:
            vision.capture_jpeg = original_capture_jpeg
            vision.post_image = original_post_image

        self.assertEqual(len(post_extras), 4)
        self.assertIsNotNone(post_extras[0])
        self.assertEqual(post_extras[0]["roi_box"], [467, 546, 807, 636])
        self.assertIsNone(post_extras[-1])
        self.assertEqual(response["ocr"][0]["text"], "fallback")

    def test_msc_explorer_close_center_uses_preview_keyword(self):
        vision = load_module()
        response = {
            "image_size": {"width": 1920, "height": 1080},
            "ocr": [
                {"text": "RK3568MSC (E:)", "center": [924, 363], "box": [872, 351, 976, 375]},
                {"text": "驱动器工具", "center": [818, 391], "box": [784, 382, 852, 400]},
                {"text": "选择要预览的文件", "center": [1438, 668], "box": [1392, 659, 1484, 677]},
            ],
        }

        self.assertEqual(vision.msc_explorer_close_center(response), (1556, 363))

    def test_wait_after_report_closes_msc_explorer_before_finish(self):
        vision = load_module()
        flow = FakeVisionFlow(
            [
                {
                    "image_size": {"width": 1920, "height": 1080},
                    "ocr": [
                        {"text": "RK3568MSC (E:)", "center": [924, 363], "box": [872, 351, 976, 375]},
                        {"text": "驱动器工具", "center": [818, 391], "box": [784, 382, 852, 400]},
                        {"text": "选择要预览的文件", "center": [1438, 668], "box": [1392, 659, 1484, 677]},
                    ],
                },
                {"ocr": [{"text": "新建患者", "center": [173, 226]}]},
            ]
        )

        asyncio.run(flow._impl.wait_and_close_msc_explorer_after_report(vision.time.monotonic()))

        self.assertEqual(flow.hid_output.clicks, [(1556, 363)])

    def test_detect_window_closes_msc_explorer_when_seen(self):
        vision = load_module()
        original_capture_jpeg = vision.capture_jpeg
        original_post_image = vision.post_image
        responses = [
            {
                "image_size": {"width": 1920, "height": 1080},
                "ocr": [
                    {"text": "RK3568MSC (E:)", "center": [924, 363], "box": [872, 351, 976, 375]},
                    {"text": "驱动器工具", "center": [818, 391], "box": [784, 382, 852, 400]},
                    {"text": "选择要预览的文件", "center": [1438, 668], "box": [1392, 659, 1484, 677]},
                ],
            },
            {"ocr": [{"text": "新建患者", "center": [173, 226]}]},
        ]
        capture_calls = []

        def fake_capture_jpeg(*args, **kwargs):
            capture_calls.append((args, kwargs))

        def fake_post_image(*args, **kwargs):
            if not responses:
                raise AssertionError("unexpected post_image call")
            return responses.pop(0)

        try:
            vision.capture_jpeg = fake_capture_jpeg
            vision.post_image = fake_post_image
            flow = vision.VisionFlow(
                SimpleNamespace(
                    enabled=True,
                    device="/dev/video9",
                    workdir="/tmp/test-vision",
                    icon_endpoint="http://127.0.0.1/icon",
                    window_endpoint="http://127.0.0.1/window",
                    software="人体成分分析仪",
                    wait_after_open=0.0,
                    wait_after_action=0.0,
                    wait_after_no_detection=5.0,
                    wait_after_start=0.0,
                    analysis_wait=0.0,
                    max_runtime=5.0,
                    timeout_seconds=1.0,
                    close_msc_popup_when_detected=True,
                ),
                FakeHidOutput(),
            )

            response = asyncio.run(flow.detect_window("probe.jpg"))
        finally:
            vision.capture_jpeg = original_capture_jpeg
            vision.post_image = original_post_image

        self.assertEqual(response["ocr"][0]["text"], "新建患者")
        self.assertEqual(flow.hid_output.clicks, [(1556, 363)])
        self.assertEqual(len(capture_calls), 2)

    def test_label_two_executes_form_and_continues_until_analysis_finish(self):
        task = {"eventClassList": [{"clickType": 0, "x": 100, "y": 443}], "patient": {"patient_id": "P1"}}
        flow = FakeVisionFlow(
            [
                {"ocr": []},
                {
                    "label": "0",
                    "ocr": [
                        {"text": "用户登录", "center": [42, 13]},
                        {"text": "用户名：", "center": [65, 150]},
                        {"text": "密码：", "center": [58, 193]},
                        {"text": "登录", "center": [110, 257]},
                    ],
                },
                {"label": "1", "ocr": [{"text": "未选择患者"}, {"text": "就绪"}, {"text": "新建患者", "center": [176, 227]}]},
                {"label": "2", "ocr": []},
                {"label": "1", "ocr": [{"text": "患者号"}, {"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}]},
                {"label": "3", "ocr": [{"text": "检查完成"}, {"text": "数据分析", "center": [400, 400]}]},
                {
                    "label": "4",
                    "ocr": [
                        {"text": "是否生成PDF报告？", "center": [260, 180]},
                        {"text": "是(Y)", "center": [210, 260]},
                        {"text": "是", "center": [220, 320]},
                    ],
                },
                {"label": "5", "ocr": [{"text": "检查报告已生成！"}, {"text": "确定", "center": [260, 260]}]},
                {"label": "1", "ocr": [{"text": "新建患者", "center": [176, 227]}]},
            ]
        )

        result = asyncio.run(flow.run_until_form_done(task))

        self.assertEqual(result, "analysis_finished")
        self.assertEqual(flow.open_count, 1)
        self.assertEqual(flow.hid_output.forms, [task])
        self.assertEqual(flow.clicked_texts, ["登录", "新建患者", "开始检查", "数据分析", "是", "确定", "新建患者"])

    def test_windows_response_keeps_label_one_ready_logic(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "1",
                    "box": [103, 104, 806, 738],
                    "ocr": [{"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}],
                }
            ]
        }

        self.assertEqual(vision.decide_action(response), ("click_text", "开始检查"))
        self.assertEqual(vision.find_ocr_center(response, "开始检查"), (300, 300))

    def test_prepare_can_restart_from_existing_ready_patient(self):
        task = {"eventClassList": [{"clickType": 0, "x": 100, "y": 443}], "patient": {"patient_id": "P1"}}
        flow = FakeVisionFlow(
            [
                {
                    "label": "1",
                    "ocr": [
                        {"text": "当前患者"},
                        {"text": "患号：P265607：年龄1科"},
                        {"text": "就绪"},
                        {"text": "开始检查", "center": [300, 300]},
                        {"text": "新建患者", "center": [176, 227]},
                    ],
                },
                {"label": "2", "ocr": []},
                {"label": "1", "ocr": [{"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}]},
                {"label": "3", "ocr": [{"text": "检查完成"}, {"text": "数据分析", "center": [400, 400]}]},
                {"label": "4", "ocr": [{"text": "是否生成PDF报告？"}, {"text": "是", "center": [220, 320]}]},
                {"label": "5", "ocr": [{"text": "检查报告已生成！"}, {"text": "确定", "center": [260, 260]}]},
                {"label": "1", "ocr": [{"text": "新建患者", "center": [176, 227]}]},
            ]
        )

        result = asyncio.run(flow.run_until_form_done(task))

        self.assertEqual(result, "analysis_finished")
        self.assertEqual(flow.hid_output.forms, [task])
        self.assertEqual(flow.clicked_texts, ["新建患者", "开始检查", "数据分析", "是", "确定", "新建患者"])

    def test_prepare_can_restart_from_completed_patient(self):
        task = {"eventClassList": [{"clickType": 0, "x": 100, "y": 443}], "patient": {"patient_id": "P1"}}
        flow = FakeVisionFlow(
            [
                {
                    "label": "1",
                    "ocr": [
                        {"text": "检查完成！"},
                        {"text": "开始检查", "center": [300, 300]},
                        {"text": "新建患者", "center": [176, 227]},
                    ],
                },
                {"label": "2", "ocr": []},
                {"label": "1", "ocr": [{"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}]},
                {"label": "3", "ocr": [{"text": "检查完成"}, {"text": "数据分析", "center": [400, 400]}]},
                {"label": "4", "ocr": [{"text": "是否生成PDF报告？"}, {"text": "是", "center": [220, 320]}]},
                {"label": "5", "ocr": [{"text": "检查报告已生成！"}, {"text": "确定", "center": [260, 260]}]},
                {"label": "1", "ocr": [{"text": "新建患者", "center": [176, 227]}]},
            ]
        )

        result = asyncio.run(flow.run_until_form_done(task))

        self.assertEqual(result, "analysis_finished")
        self.assertEqual(flow.hid_output.forms, [task])
        self.assertEqual(flow.clicked_texts, ["新建患者", "开始检查", "数据分析", "是", "确定", "新建患者"])

    def test_label_zero_wins_over_label_one_when_both_are_detected(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "0",
                    "ocr": [
                        {"text": "用户登录", "center": [42, 13]},
                        {"text": "用户名：", "center": [65, 150]},
                        {"text": "密码：", "center": [58, 193]},
                        {"text": "登录", "center": [110, 259]},
                    ],
                },
                {
                    "label": "1",
                    "ocr": [
                        {"text": "登录", "center": [113, 257]},
                        {"text": "未选择患者", "center": [456, 557]},
                        {"text": "新建患者", "center": [176, 227]},
                    ],
                },
            ]
        }

        self.assertEqual(vision.decide_action(response), ("click_text", "登录"))

    def test_label_three_plus_label_four_prefers_confirm_dialog(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "3",
                    "box": [109, 106, 808, 745],
                    "ocr": [{"text": "检查完成"}, {"text": "数据分析", "center": [400, 400]}],
                },
                {"label": "4", "box": [0, 0, 391, 310], "ocr": [{"text": "是(Y)", "center": [500, 500]}]},
            ]
        }

        self.assertEqual(vision.decide_action(response), ("click_text", "是(Y)"))
        self.assertEqual(vision.find_ocr_center(response, "是(Y)"), (500, 500))

    def test_label_five_plus_label_three_prefers_confirm_dialog(self):
        vision = load_module()
        response = {
            "windows": [
                {"label": "5", "box": [0, 0, 391, 310], "ocr": [{"text": "确定", "center": [260, 260]}]},
                {
                    "label": "3",
                    "box": [109, 106, 808, 745],
                    "ocr": [{"text": "检查完成"}, {"text": "数据分析", "center": [400, 400]}],
                },
            ]
        }

        self.assertEqual(vision.decide_action(response), ("click_text", "确定"))
        self.assertEqual(vision.find_ocr_center(response, "确定"), (260, 260))

    def test_after_analysis_label_three_plus_label_four_prefers_confirm_dialog(self):
        vision = load_module()
        response = {
            "windows": [
                {"label": "3", "ocr": [{"text": "检查完成"}]},
                {"label": "4", "ocr": [{"text": "是(Y)", "center": [500, 500]}]},
            ]
        }

        self.assertEqual(vision.decide_after_analysis(response), ("click_text", "是(Y)"))

    def test_merged_label_four_uses_confirm_when_yes_is_absent(self):
        vision = load_module()
        response = {
            "windows": [
                {"label": "3", "ocr": [{"text": "检查完成"}]},
                {"label": "4", "ocr": [{"text": "确认", "center": [280, 260]}]},
            ]
        }

        self.assertEqual(vision.decide_after_analysis(response), ("click_text", "确认"))
        self.assertEqual(vision.find_ocr_center(response, "确认"), (280, 260))

    def test_pdf_report_label_four_chooses_lowest_yes_text(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "4",
                    "ocr": [
                        {"text": "是否生成PDF报告？", "center": [260, 180]},
                        {"text": "是(Y)", "center": [210, 260]},
                        {"text": "是", "center": [220, 320]},
                        {"text": "否(N)", "center": [310, 320]},
                    ],
                }
            ]
        }

        self.assertEqual(vision.decide_after_analysis(response), ("click_text", "是"))
        self.assertEqual(vision.find_ocr_center(response, "是"), (220, 320))

    def test_pdf_report_prompt_accepts_ocr_spaces(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "4",
                    "ocr": [
                        {"text": "是否生成 PDF 报告?", "center": [960, 524]},
                        {"text": "是(Y)", "center": [922, 602]},
                        {"text": "否(N)", "center": [1016, 602]},
                    ],
                }
            ]
        }

        self.assertTrue(vision.is_pdf_report_prompt(response))
        self.assertEqual(vision.pdf_report_yes_target(response), "是(Y)")

    def test_report_generated_accepts_label_one_with_confirm_text(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "1",
                    "ocr": [
                        {"text": "检查报告已生成！", "center": [893, 516]},
                        {"text": "确定", "center": [1073, 610]},
                    ],
                }
            ]
        }

        self.assertTrue(vision.is_report_generated(response))

    def test_generic_window_label_uses_ocr_for_login(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "1",
                    "ocr": [
                        {"text": "用户登录", "center": [42, 13]},
                        {"text": "用户名：", "center": [65, 150]},
                        {"text": "密码：", "center": [58, 193]},
                        {"text": "登录", "center": [110, 259]},
                    ],
                }
            ]
        }

        self.assertTrue(vision.is_login_window(response))
        self.assertEqual(vision.decide_action(response), ("click_text", "登录"))

    def test_generic_window_label_uses_ocr_for_new_patient_dialog(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "0",
                    "ocr": [
                        {"text": "新建患者", "center": [48, 13]},
                        {"text": "患者号:", "center": [45, 58]},
                        {"text": "姓名：", "center": [39, 121]},
                        {"text": "性别:", "center": [39, 186]},
                        {"text": "年龄：", "center": [39, 250]},
                        {"text": "确认", "center": [99, 443]},
                    ],
                }
            ]
        }

        self.assertTrue(vision.is_new_patient_window(response))
        self.assertEqual(vision.decide_action(response), ("form_input", None))

    def test_order_department_window_starts_form_before_main_window_actions(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "0",
                    "ocr": [
                        {"text": "当前患者", "center": [159, 523]},
                        {"text": "未选择患者", "center": [456, 557]},
                        {"text": "检查进度", "center": [159, 599]},
                        {"text": "就绪", "center": [456, 664]},
                        {"text": "新建患者", "center": [176, 227]},
                    ],
                },
                {
                    "label": "0",
                    "ocr": [
                        {"text": "患者号:", "center": [45, 58]},
                        {"text": "开单科室：", "center": [50, 313]},
                        {"text": "确认", "center": [99, 443]},
                    ],
                },
            ]
        }

        self.assertTrue(vision.is_new_patient_window(response))
        self.assertEqual(vision.decide_action(response), ("form_input", None))

    def test_generic_window_label_uses_ocr_for_pdf_prompt(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "1",
                    "ocr": [
                        {"text": "选择报告类型", "center": [902, 474]},
                        {"text": "是否生成 PDF 报告?", "center": [960, 524]},
                        {"text": "是(Y)", "center": [922, 602]},
                        {"text": "否(N)", "center": [1016, 602]},
                    ],
                }
            ]
        }

        self.assertTrue(vision.is_pdf_report_prompt(response))
        self.assertEqual(vision.decide_after_analysis(response), ("click_text", "是(Y)"))

    def test_generic_window_label_uses_ocr_for_report_generated(self):
        vision = load_module()
        response = {
            "windows": [
                {
                    "label": "1",
                    "ocr": [
                        {"text": "分析完成", "center": [834, 467]},
                        {"text": "检查报告已生成！", "center": [893, 516]},
                        {"text": "确定", "center": [1073, 610]},
                    ],
                }
            ]
        }

        self.assertTrue(vision.is_report_generated(response))
        self.assertEqual(vision.decide_after_analysis(response), ("click_text", "确定"))

    def test_start_check_waits_until_hid_form_input_has_completed(self):
        task = {"eventClassList": [{"clickType": 0, "x": 100, "y": 443}], "patient": {"patient_id": "P1"}}
        flow = FakeVisionFlow(
            [
                {"label": "1", "ocr": [{"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}]},
                {"label": "2", "ocr": []},
                {"label": "1", "ocr": [{"text": "患者号"}, {"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}]},
                {"label": "3", "ocr": [{"text": "检查完成"}, {"text": "数据分析", "center": [400, 400]}]},
                {
                    "label": "4",
                    "ocr": [{"text": "是否生成PDF报告？"}, {"text": "是", "center": [220, 320]}],
                },
                {"label": "5", "ocr": [{"text": "检查报告已生成！"}, {"text": "确定", "center": [260, 260]}]},
                {"label": "1", "ocr": [{"text": "新建患者", "center": [176, 227]}]},
            ]
        )

        result = asyncio.run(flow.run_until_form_done(task))

        self.assertEqual(result, "analysis_finished")
        self.assertEqual(flow.hid_output.forms, [task])
        self.assertEqual(flow.clicked_texts, ["开始检查", "数据分析", "是", "确定", "新建患者"])

    def test_start_check_uses_ocr_even_when_label_is_three(self):
        vision = load_module()
        response = {"label": "3", "ocr": [{"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}]}

        self.assertEqual(vision.decide_action(response), ("click_text", "开始检查"))

    def test_start_check_ready_does_not_require_exact_patient_id_ocr(self):
        vision = load_module()
        response = {
            "label": "1",
            "ocr": [
                {"text": "患号：P265607：年龄1科"},
                {"text": "就绪"},
                {"text": "开始检查", "center": [300, 300]},
            ],
        }

        self.assertTrue(vision.is_ready_to_start_check(response))

    def test_check_complete_uses_ocr_even_when_label_is_one(self):
        vision = load_module()
        response = {"label": "1", "ocr": [{"text": "检查完成！"}, {"text": "数据分析", "center": [400, 400]}]}

        self.assertEqual(vision.decide_action(response), ("analysis", None))

    def test_linear_flow_starts_directly_from_label_two(self):
        task = {"eventClassList": [{"clickType": 0, "x": 100, "y": 443}], "patient": {"patient_id": "P1"}}
        flow = FakeVisionFlow(
            [
                {"label": "2", "ocr": []},
                {"label": "1", "ocr": [{"text": "患者号"}, {"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}]},
                {"label": "1", "ocr": [{"text": "检查完成！"}, {"text": "数据分析", "center": [400, 400]}]},
                {
                    "label": "4",
                    "ocr": [{"text": "是否生成PDF报告？"}, {"text": "是", "center": [220, 320]}],
                },
                {"label": "5", "ocr": [{"text": "检查报告已生成！"}, {"text": "确定", "center": [260, 260]}]},
                {"label": "1", "ocr": [{"text": "新建患者", "center": [176, 227]}]},
            ]
        )

        result = asyncio.run(flow.run_until_form_done(task))

        self.assertEqual(result, "analysis_finished")
        self.assertEqual(flow.hid_output.forms, [task])
        self.assertEqual(flow.open_count, 0)
        self.assertEqual(flow.clicked_texts, ["开始检查", "数据分析", "是", "确定", "新建患者"])

    def test_linear_flow_starts_directly_from_generic_label_zero_order_department_form(self):
        task = {"eventClassList": [{"clickType": 0, "x": 100, "y": 443}], "patient": {"patient_id": "P1"}}
        flow = FakeVisionFlow(
            [
                {
                    "label": "0",
                    "ocr": [
                        {"text": "患者号:", "center": [45, 58]},
                        {"text": "开单科室：", "center": [50, 313]},
                        {"text": "确认", "center": [99, 443]},
                    ],
                },
                {"label": "0", "ocr": [{"text": "患者号"}, {"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}]},
                {"label": "0", "ocr": [{"text": "检查完成！"}, {"text": "数据分析", "center": [400, 400]}]},
                {
                    "label": "0",
                    "ocr": [{"text": "是否生成PDF报告？"}, {"text": "是", "center": [220, 320]}],
                },
                {"label": "0", "ocr": [{"text": "检查报告已生成！"}, {"text": "确定", "center": [260, 260]}]},
                {"label": "0", "ocr": [{"text": "新建患者", "center": [176, 227]}]},
            ]
        )

        result = asyncio.run(flow.run_until_form_done(task))

        self.assertEqual(result, "analysis_finished")
        self.assertEqual(flow.hid_output.forms, [task])
        self.assertEqual(flow.open_count, 0)
        self.assertEqual(flow.clicked_texts, ["开始检查", "数据分析", "是", "确定", "新建患者"])

    def test_icon_not_found_waits_longer_and_retries(self):
        task = {"eventClassList": [], "patient": {"patient_id": "P1"}}
        flow = FakeVisionFlow(
            [
                {"ocr": []},
                {"ocr": []},
                {"label": "2", "ocr": []},
                {"label": "1", "ocr": [{"text": "患者号"}, {"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}]},
                {"label": "3", "ocr": [{"text": "检查完成"}, {"text": "数据分析", "center": [400, 400]}]},
                {"label": "4", "ocr": [{"text": "是否生成PDF报告？"}, {"text": "是", "center": [220, 320]}]},
                {"label": "5", "ocr": [{"text": "检查报告已生成！"}, {"text": "确定", "center": [260, 260]}]},
                {"label": "1", "ocr": [{"text": "新建患者", "center": [176, 227]}]},
            ],
            open_results=[False, True],
        )

        result = asyncio.run(flow.run_until_form_done(task))

        self.assertEqual(result, "analysis_finished")
        self.assertEqual(flow.open_count, 2)
        self.assertIn(5.0, flow.sleeps)
        self.assertEqual(flow.hid_output.forms, [task])

    def test_empty_detection_after_successful_open_waits_instead_of_opening_again(self):
        task = {"eventClassList": [], "patient": {"patient_id": "P1"}}
        flow = FakeVisionFlow(
            [
                {"ocr": []},
                {
                    "label": "0",
                    "ocr": [
                        {"text": "用户登录", "center": [42, 13]},
                        {"text": "用户名：", "center": [65, 150]},
                        {"text": "密码：", "center": [58, 193]},
                        {"text": "登录", "center": [110, 257]},
                    ],
                },
                {"ocr": []},
                {"label": "1", "ocr": [{"text": "未选择患者"}, {"text": "就绪"}, {"text": "新建患者", "center": [176, 227]}]},
                {"label": "2", "ocr": []},
                {"label": "1", "ocr": [{"text": "患者号"}, {"text": "就绪"}, {"text": "开始检查", "center": [300, 300]}]},
                {"label": "3", "ocr": [{"text": "检查完成"}, {"text": "数据分析", "center": [400, 400]}]},
                {"label": "4", "ocr": [{"text": "是否生成PDF报告？"}, {"text": "是", "center": [220, 320]}]},
                {"label": "5", "ocr": [{"text": "检查报告已生成！"}, {"text": "确定", "center": [260, 260]}]},
                {"label": "1", "ocr": [{"text": "新建患者", "center": [176, 227]}]},
            ],
            open_results=[True, True],
        )

        result = asyncio.run(flow.run_until_form_done(task))

        self.assertEqual(result, "analysis_finished")
        self.assertEqual(flow.open_count, 1)
        self.assertEqual(flow.clicked_texts, ["登录", "新建患者", "开始检查", "数据分析", "是", "确定", "新建患者"])

    def test_bodypass_input_center_uses_main_window_relative_offsets(self):
        vision = load_module()
        window = {
            "box": [467, 166, 1479, 895],
            "ocr": [{"text": vision.BODYPASS_TITLE_TEXTS[0], "center": [575, 186]}],
        }

        self.assertEqual(vision.bodypass_input_center(window, vision.BODYPASS_MEMBER_ID_TEXT), (685, 362))
        self.assertEqual(vision.bodypass_input_center(window, vision.BODYPASS_MEMBER_NAME_TEXT), (685, 390))
        self.assertEqual(vision.bodypass_input_center(window, vision.BODYPASS_MEMBER_BIRTHDAY_TEXT), (946, 390))

    def test_bodypass_patient_birthday_falls_back_to_date_parts(self):
        vision = load_module()

        self.assertEqual(
            vision.bodypass_patient_birthday({"nian": "1991", "yue": "6", "ri": "8"}),
            "1991-06-08",
        )

    def test_bodypass_already_open_checks_light_main_window_before_opening_icon(self):
        vision = load_module()
        main = vision.bodypass_main_light_response(
            {
                "ocr": [
                    {
                        "text": vision.BODYPASS_TITLE_TEXTS[0],
                        "center": [575, 186],
                    }
                ]
            }
        )
        flow = FakeVisionFlow([main], flow="bodypass")

        response = asyncio.run(
            flow._impl.prepare_bodypass_main_window(vision.time.monotonic())
        )

        self.assertTrue(vision.is_bodypass_main_window(response))
        self.assertEqual(flow.light_count, 1)
        self.assertEqual(flow.open_count, 0)

    def test_bodypass_flow_opens_inputs_member_and_prints_result(self):
        task = {
            "scan_text": "P2605260007",
            "eventClassList": [],
            "patient": {"patient_id": "P2605260007", "patient_name": "张三", "birthday": "1991-06-08"},
        }
        main = {
            "windows": [
                {
                    "label": "0",
                    "box": [467, 166, 1479, 895],
                    "ocr": [
                        {"text": "人体成分数据管理程序（BodyPass）", "center": [575, 186]},
                        {"text": "编号", "center": [505, 362], "box": [488, 353, 523, 372]},
                        {"text": "姓名", "center": [505, 390], "box": [488, 379, 523, 401]},
                    ],
                }
            ]
        }
        result_ready = {
            "ocr": [
                {"text": "Machine State=显示检测结果", "center": [616, 598]},
            ]
        }
        flow = FakeVisionFlow(
            [
                {"ocr": []},
                main,
                result_ready,
                {"ocr": [{"text": "检测结果明细", "center": [550, 180]}, {"text": "预览检测结果", "center": [900, 800]}]},
                {"ocr": [{"text": "人体成分分析报告", "center": [778, 363]}]},
                {
                    "windows": [
                        {
                            "box": [473, 152, 1435, 938],
                            "ocr": [
                                {"text": "选择打印机", "center": [545, 457]},
                                {"text": "打印（P)", "center": [866, 873]},
                                {"text": "取消", "center": [963, 872]},
                            ],
                        }
                    ]
                },
                {"ocr": [{"text": "人体成分分析报告", "center": [778, 363]}]},
                {"ocr": [{"text": "检测结果明细", "center": [550, 180]}]},
            ],
            flow="bodypass",
        )

        result = asyncio.run(flow.run_until_form_done(task))

        self.assertEqual(result, "bodypass_finished")
        self.assertEqual(flow.open_count, 1)
        self.assertEqual(flow.light_count, 2)
        self.assertEqual(
            flow.hid_output.inputs,
            [
                ("P2605260007", 685, 362, "bodypass_patient_id"),
                ("张三", 685, 390, "bodypass_patient_name"),
            ],
        )
        self.assertEqual(flow.hid_output.cleared_inputs, [("1991-06-08", 946, 390, "bodypass_birthday")])
        self.assertEqual(
            flow.hid_output.clicks,
            [
                (1287, 260),
                (1037, 260),
                (900, 800),
                (1257, 244),
                (866, 873),
                (1390, 244),
                (1387, 356),
            ],
        )


if __name__ == "__main__":
    unittest.main()
