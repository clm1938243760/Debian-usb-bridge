import asyncio
import importlib.util
import sys
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

    async def click(self, x, y):
        self.clicks.append((x, y))

    async def double_click(self, x, y):
        self.clicks.append((x, y, "double"))

    async def execute_form(self, task):
        self.forms.append(task)


class FakeVisionFlow:
    def __init__(self, responses, open_results=None):
        vision = load_module()
        self._impl = vision.VisionFlow(
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
            ),
            FakeHidOutput(),
        )
        self.responses = list(responses)
        self.open_results = list(open_results or [])
        self.open_count = 0
        self.clicked_texts = []
        self.sleeps = []
        self._impl.detect_window = self.detect_window
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

    def test_label_two_executes_form_and_continues_until_analysis_finish(self):
        task = {"eventClassList": [{"clickType": 0, "x": 100, "y": 443}], "patient": {"patient_id": "P1"}}
        flow = FakeVisionFlow(
            [
                {"ocr": []},
                {"label": "0", "ocr": [{"text": "登录", "center": [110, 257]}]},
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

    def test_label_zero_wins_over_label_one_when_both_are_detected(self):
        vision = load_module()
        response = {
            "windows": [
                {"label": "0", "ocr": [{"text": "登录", "center": [110, 259]}]},
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
                {"label": "0", "ocr": [{"text": "登录", "center": [110, 257]}]},
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


if __name__ == "__main__":
    unittest.main()
