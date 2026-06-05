import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fb_status.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fb_status", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FramebufferStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.renderer = cls.module.AssetRenderer(ROOT / "智能体UI")

    def test_main_states_fill_the_480_by_320_screen_without_black_edges(self):
        states = [
            {"screen": "wait_scan"},
            {
                "screen": "select_item",
                "scan": "P2605260007",
                "items": [
                    {"exam_item": "人体成分检查"},
                    {"exam_item": "体脂分析"},
                    {"exam_item": "健康评估"},
                    {"exam_item": "复查"},
                ],
                "selected_index": 0,
            },
            {"screen": "wait_report"},
            {"screen": "inputting", "exam_item": "人体成分检查"},
            {"screen": "upload_done"},
            {"screen": "not_found"},
        ]

        for state in states:
            with self.subTest(screen=state["screen"]):
                image = self.renderer.render({"display": state})
                self.assertEqual((480, 320), image.size)
                for point in ((0, 0), (479, 0), (0, 319), (479, 319)):
                    pixel = image.getpixel(point)
                    self.assertGreater(sum(pixel[:3]), 100)

    def test_connected_boot_state_uses_the_same_full_screen_size(self):
        image = self.renderer.render_boot("ok", self.module.TEXT["connected_subtitle"])
        self.assertEqual((480, 320), image.size)
        self.assertGreater(sum(image.getpixel((0, 0))[:3]), 100)


if __name__ == "__main__":
    unittest.main()
