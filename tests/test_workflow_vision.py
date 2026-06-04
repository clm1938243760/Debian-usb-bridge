import asyncio
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

aiohttp_stub = ModuleType("aiohttp")
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = lambda **kwargs: None
sys.modules.setdefault("aiohttp", aiohttp_stub)

yaml_stub = ModuleType("yaml")
yaml_stub.safe_load = lambda handle: {}
sys.modules.setdefault("yaml", yaml_stub)

from rk3588_gateway.workflow import GatewayWorkflow


class FakeQueue:
    def put(self, event):
        return None


class FakeHidOutput:
    def __init__(self):
        self.forms = []

    async def execute_form(self, task):
        self.forms.append(task)


class FakeVisionFlow:
    def __init__(self):
        self.tasks = []

    async def run_until_form_done(self, task, on_hid_start=None):
        if on_hid_start:
            on_hid_start()
        self.tasks.append(task)
        return "analysis_finished"


def make_config(vision_enabled=True):
    return SimpleNamespace(
        device=SimpleNamespace(id="dev1", type="人体成分检查"),
        patient_api=SimpleNamespace(enabled=False, endpoint="", timeout_seconds=1, user_agent="test", raw_dir="/tmp"),
        hid_input=SimpleNamespace(
            enabled=True,
            keyboard_backend="usb_gadget",
            mouse_backend="usb_gadget",
            keyboard_device="/dev/hidg0",
            mouse_device="/dev/hidg1",
            ch9350_serial_device="",
            ch9350_baudrate=115200,
            ch9350_state=0,
            ch9350_set_state2=False,
            ch9350_caps_led_mask=1,
            ch9350_mouse_frame="absolute7",
            ch9350_mouse_reset_to_origin=False,
            template_path="/tmp/template.json",
            screen_width=1920,
            screen_height=1080,
            action_delay_ms=1,
            start_delay_ms=1,
            force_caps_ascii=True,
            non_ascii_mode="powershell",
            powershell_wait_ms=1,
        ),
        vision=SimpleNamespace(
            enabled=vision_enabled,
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
    )


class WorkflowVisionTest(unittest.TestCase):
    def test_execute_input_task_uses_vision_flow_when_enabled(self):
        workflow = GatewayWorkflow(make_config(True), FakeQueue())
        workflow.hid_output = FakeHidOutput()
        workflow.vision_flow = FakeVisionFlow()
        task = {"eventClassList": [], "patient": {"patient_id": "P1"}}

        started = []
        result = asyncio.run(workflow._execute_input_task(task, on_hid_start=lambda: started.append(True)))

        self.assertEqual(result, "analysis_finished")
        self.assertEqual(workflow.vision_flow.tasks, [task])
        self.assertEqual(workflow.hid_output.forms, [])
        self.assertEqual(started, [True])


if __name__ == "__main__":
    unittest.main()
