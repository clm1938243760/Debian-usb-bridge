import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "open_login_probe.py"


def load_module():
    spec = importlib.util.spec_from_file_location("open_login_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OpenLoginProbeTest(unittest.TestCase):
    def test_build_capture_command_defaults_to_uvc_mjpg_stable_frame(self):
        probe = load_module()
        args = SimpleNamespace(
            device="/dev/video9",
            capture_io_mode=2,
            capture_frames=30,
            capture_format="mjpg",
            capture_width=1920,
            capture_height=1080,
            capture_framerate=30,
        )

        cmd = probe.build_capture_command(args, Path("/tmp/open/window.jpg"))

        self.assertIn("device=/dev/video9", cmd)
        self.assertIn("io-mode=2", cmd)
        self.assertIn("num-buffers=30", cmd)
        self.assertIn("image/jpeg,width=1920,height=1080,framerate=30/1", cmd)
        self.assertIn("location=/tmp/open/.window_%02d.jpg", cmd)

    def test_extract_center_accepts_icon_response(self):
        probe = load_module()
        self.assertEqual(probe.extract_center({"center": [267, 924]}, "center"), (267, 924))

    def test_find_ocr_center_uses_exact_login_text(self):
        probe = load_module()
        response = {
            "label": "0",
            "ocr": [
                {"text": "取消", "center": [244, 258]},
                {"text": "登录", "center": [110, 257]},
            ],
        }
        self.assertEqual(probe.find_ocr_center(response, "登录"), (110, 257))

    def test_should_click_login_only_for_label_zero(self):
        probe = load_module()
        self.assertTrue(probe.should_click_login({"label": "0"}))
        self.assertFalse(probe.should_click_login({"label": "1"}))
        self.assertFalse(probe.should_click_login({"label": None}))

    def test_build_image_body_includes_optional_software(self):
        probe = load_module()
        body = probe.build_image_body("abc123", {"software": "人体成分分析仪"})
        self.assertEqual(
            __import__("json").loads(body.decode("utf-8")),
            {"image_base64": "abc123", "software": "人体成分分析仪"},
        )

    def test_decide_action_opens_when_label_missing(self):
        probe = load_module()
        self.assertEqual(probe.decide_action({"ocr": []}), ("open", None))

    def test_decide_action_waits_when_loading_without_label(self):
        probe = load_module()
        self.assertEqual(probe.decide_action({"ocr": [{"text": "正在加载"}]}), ("wait", None))

    def test_decide_action_clicks_login_for_label_zero(self):
        probe = load_module()
        self.assertEqual(probe.decide_action({"label": "0", "ocr": []}), ("click_text", "登录"))

    def test_decide_action_handles_main_window_patient_and_ready_states(self):
        probe = load_module()
        self.assertEqual(
            probe.decide_action({"label": "1", "ocr": [{"text": "未选择患者"}]}),
            ("click_text", "新建患者"),
        )
        self.assertEqual(
            probe.decide_action({"label": "1", "ocr": [{"text": "就绪"}]}),
            ("click_text", "开始检查"),
        )

    def test_decide_action_handles_patient_analysis_and_dialogs(self):
        probe = load_module()
        self.assertEqual(
            probe.decide_action({"label": "2", "ocr": []}),
            ("scanner_stage", None),
        )
        self.assertEqual(
            probe.decide_action({"label": "3", "ocr": [{"text": "检查完成"}]}),
            ("analysis", None),
        )
        self.assertEqual(probe.decide_action({"label": "4", "ocr": []}), ("click_text", "确定"))
        self.assertEqual(probe.decide_action({"label": "5", "ocr": []}), ("click_text", "确定"))

    def test_decide_after_analysis_finishes_if_label_three_still_present(self):
        probe = load_module()
        self.assertEqual(
            probe.decide_after_analysis({"label": "3", "ocr": [{"text": "检查完成"}]}),
            ("finish", None),
        )
        self.assertEqual(probe.decide_after_analysis({"label": "4", "ocr": []}), ("click_text", "确定"))

    def test_decide_after_analysis_waits_on_loading_or_unknown_state(self):
        probe = load_module()
        self.assertEqual(
            probe.decide_after_analysis({"ocr": [{"text": "正在加载"}]}),
            ("wait", None),
        )
        self.assertEqual(probe.decide_after_analysis({"label": "1", "ocr": []}), ("wait", None))

    def test_close_center_prefers_ocr_close_then_box(self):
        probe = load_module()
        self.assertEqual(
            probe.close_center({"ocr": [{"text": "×", "center": [368, 14]}], "box": [0, 0, 391, 310]}),
            (368, 14),
        )
        self.assertEqual(probe.close_center({"ocr": [], "box": [10, 20, 410, 320]}), (390, 35))


if __name__ == "__main__":
    unittest.main()
