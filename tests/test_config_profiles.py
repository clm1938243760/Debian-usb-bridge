import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

yaml_stub = ModuleType("yaml")
yaml_stub.safe_load = json.load
sys.modules.setdefault("yaml", yaml_stub)

from rk3588_gateway.config import load_config


def base_config():
    return {
        "device": {"id": "dev1", "location": "bench", "type": "人体成分检查", "profile_dir": "/tmp/device"},
        "scanner": {"enabled": False},
        "patient_api": {"enabled": False},
        "hid_input": {"enabled": True},
        "vision": {
            "enabled": True,
            "software": "人体成分分析仪",
            "icon_endpoint": "http://127.0.0.1/icon",
            "window_endpoint": "http://127.0.0.1/window",
        },
        "printer": {"enabled": False},
        "print_capture": {"enabled": False},
        "vm_transfer": {"enabled": False},
        "uploader": {"enabled": False},
        "local_api": {"enabled": True},
        "storage": {"sqlite_path": "/tmp/events.db"},
        "logging": {"level": "INFO"},
    }


def write_yaml(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ConfigProfilesTest(unittest.TestCase):
    def test_legacy_config_without_profile_still_uses_device_and_vision_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            payload = base_config()
            write_yaml(config_path, payload)

            config = load_config(config_path)

        self.assertEqual(config.active_profile, "")
        self.assertEqual(config.device.type, "人体成分检查")
        self.assertEqual(config.vision.software, "人体成分分析仪")
        self.assertEqual(config.vision.flow, "body_composition")

    def test_active_profile_file_overrides_device_type_and_software(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles_dir = root / "profiles"
            profiles_dir.mkdir()
            write_yaml(
                profiles_dir / "body_composition.yaml",
                {
                    "id": "body_composition",
                    "device_type": "人体成分检查",
                    "software": "人体成分分析仪",
                    "flow": "body_composition",
                    "vision": {"close_msc_popup_when_detected": True, "wait_after_open": 3.0},
                },
            )
            payload = base_config()
            payload["device"]["type"] = "另一个检查"
            payload["vision"]["software"] = "另一个软件"
            payload["vision"]["wait_after_open"] = 1.0
            payload["active_profile"] = "body_composition"
            payload["profile_files"] = ["profiles/body_composition.yaml"]
            config_path = root / "config.yaml"
            write_yaml(config_path, payload)

            config = load_config(config_path)

        self.assertEqual(config.active_profile, "body_composition")
        self.assertEqual(config.device.type, "人体成分检查")
        self.assertEqual(config.vision.software, "人体成分分析仪")
        self.assertEqual(config.vision.flow, "body_composition")
        self.assertTrue(config.vision.close_msc_popup_when_detected)
        self.assertEqual(config.vision.wait_after_open, 3.0)

    def test_active_inline_profile_can_switch_to_another_software(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = base_config()
            payload["active_profile"] = "other"
            payload["profiles"] = {
                "other": {
                    "device_type": "另一个检查",
                    "software": "另一个软件",
                    "flow": "other_flow",
                    "vision": {"close_msc_popup_when_detected": False},
                }
            }
            config_path = Path(temp_dir) / "config.yaml"
            write_yaml(config_path, payload)

            config = load_config(config_path)

        self.assertEqual(config.device.type, "另一个检查")
        self.assertEqual(config.vision.software, "另一个软件")
        self.assertEqual(config.vision.flow, "other_flow")
        self.assertFalse(config.vision.close_msc_popup_when_detected)


if __name__ == "__main__":
    unittest.main()
