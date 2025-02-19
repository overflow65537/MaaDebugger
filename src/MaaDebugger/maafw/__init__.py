import re
from asyncify import asyncify
from pathlib import Path
from typing import Callable, List, Optional, Union

from maa.controller import AdbController, Win32Controller
from maa.tasker import Tasker, RecognitionDetail, NotificationHandler
from maa.resource import Resource
from maa.toolkit import Toolkit, AdbDevice, DesktopWindow
from PIL import Image
import os
from typing import Dict
import json

import importlib.util
from ..utils import cvmat_to_image


class MaaFW:

    resource: Optional[Resource]
    controller: Union[AdbController, Win32Controller, None]
    tasker: Optional[Tasker]
    notification_handler: Optional[NotificationHandler]

    def __init__(self):
        Toolkit.init_option("./")
        Tasker.set_debug_mode(True)

        self.resource = None
        self.controller = None
        self.tasker = None

        self.screenshotter = Screenshotter(self.screencap)
        self.notification_handler = None
    def load_custom_objects(self, custom_dir):
        if not os.path.exists(custom_dir):
            print(f"自定义文件夹 {custom_dir} 不存在")
            return
        if not os.listdir(custom_dir):
            print(f"自定义文件夹 {custom_dir} 为空")
            return
        if os.path.exists(os.path.join(custom_dir, "custom.json")):
            print("配置文件方案")
            with open(os.path.join(custom_dir, "custom.json"), "r", encoding="utf-8") as MAA_Config:
                custom_config: Dict[str, Dict] = json.load(MAA_Config)
            
            for custom_name, custom in custom_config.items():
                custom_type:str = custom.get("type")
                custom_class_name:str = custom.get("class")
                custom_file_path:str = custom.get("file_path")
                if '{custom_path}' in custom_file_path:
                    custom_file_path = custom_file_path.replace("{custom_path}", str(custom_dir))


                if not all([custom_type, custom_name, custom_class_name, custom_file_path]):
                    print(f"配置项 {custom} 缺少必要信息，跳过")
                    continue

                try:
                    print(f"custom_type: {custom_type}, custom_name: {custom_name}, custom_class_name: {custom_class_name}, custom_file_path: {custom_file_path}")
                    module_name = os.path.splitext(os.path.basename(custom_file_path))[0]
                    # 动态导入模块
                    spec = importlib.util.spec_from_file_location(module_name, custom_file_path)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    print(f"模块 {module} 导入成功")

                    # 获取类对象
                    class_obj = getattr(module, custom_class_name)

                    # 实例化类
                    instance = class_obj()

                    if custom_type == "action":
                        if self.resource.register_custom_action(custom_name, instance):
                            print(f"加载自定义动作{custom_name}")
                
                    elif custom_type == "recognition":
                        if self.resource.register_custom_recognition(custom_name, instance):
                            print(f"加载自定义识别器{custom_name}")
        
                except (ImportError, AttributeError, FileNotFoundError) as e:
                    print(f"加载自定义 {custom_name} 时出错: {e}")


        for module_type in ["action", "recognition"]:
            
            module_type_dir = os.path.join(custom_dir, module_type)
            if not os.path.exists(module_type_dir):
                print(f"{module_type} 文件夹不存在于 {custom_dir}")
                continue
            print(f"文件夹方案{module_type}")
            for subdir in os.listdir(module_type_dir):
                subdir_path = os.path.join(module_type_dir, subdir)
                if os.path.isdir(subdir_path):
                    entry_file = os.path.join(subdir_path, "main.py")
                    if not os.path.exists(entry_file):
                        print(f"{subdir_path} 没有main.py")
                        continue  # 如果没有找到main.py，则跳过该子目录

                    try:

                        module_name = subdir  # 使用子目录名作为模块名
                        spec = importlib.util.spec_from_file_location(
                            module_name, entry_file
                        )
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        if module_type == "action":
                            if self.resource.register_custom_action(
                                f"{module_name}", getattr(module, module_name)()
                            ):
                                print(
                                    f"加载自定义动作{module_name},{getattr(module, module_name)()}"
                                )
                        elif module_type == "recognition":
                            if self.resource.register_custom_recognition(
                                f"{module_name}", getattr(module, module_name)()
                            ):
                                print(f"加载自定义识别器{module_name}")
                    except Exception as e:
                        print(f"加载自定义内容时发生错误{entry_file}: {e}")
    @staticmethod
    @asyncify
    def detect_adb() -> List[AdbDevice]:
        return Toolkit.find_adb_devices()

    @staticmethod
    @asyncify
    def detect_win32hwnd(window_regex: str) -> List[DesktopWindow]:
        windows = Toolkit.find_desktop_windows()
        result = []
        for win in windows:
            if not re.search(window_regex, win.window_name):
                continue

            result.append(win)

        return result

    @asyncify
    def connect_adb(self, path: Path, address: str, config: dict) -> bool:
        self.controller = AdbController(path, address, config=config)
        connected = self.controller.post_connection().wait().succeeded
        if not connected:
            print(f"Failed to connect {path} {address}")
            return False

        return True

    @asyncify
    def connect_win32hwnd(
        self, hwnd: Union[int, str], screencap_method: int, input_method: int
    ) -> bool:
        if isinstance(hwnd, str):
            hwnd = int(hwnd, 16)

        self.controller = Win32Controller(
            hwnd, screencap_method=screencap_method, input_method=input_method
        )
        connected = self.controller.post_connection().wait().succeeded
        if not connected:
            print(f"Failed to connect {hwnd}")
            return False

        return True

    @asyncify
    def load_resource(self, dir: List[Path]) -> bool:
        if not self.resource:
            self.resource = Resource()
        self.custom_path = []
        self.resource.clear()
        for d in dir:
            if not d.exists():
                return False

            status = self.resource.post_bundle(d).wait().succeeded
            if not status:
                return False
            self.custom_path.append(d)
        return True

    @asyncify
    def run_task(self, entry: str, pipeline_override: dict = {}) -> bool:
        if not self.tasker:
            self.tasker = Tasker(notification_handler=self.notification_handler)

        if not self.resource or not self.controller:
            print("Resource or Controller not initialized")
            return False

        self.tasker.bind(self.resource, self.controller)
        if not self.tasker.inited:
            print("Failed to init MaaFramework tasker")
            return False
        self.resource.clear_custom_recognition()
        self.resource.clear_custom_recognition()
        for d in self.custom_path:
            self.load_custom_objects(d.parent.parent / "custom")
        return self.tasker.post_task(entry, pipeline_override).wait().succeeded

    @asyncify
    def stop_task(self):
        if not self.tasker:
            return

        self.tasker.post_stop().wait()

    @asyncify
    def screencap(self, capture: bool = True) -> Optional[Image.Image]:
        if not self.controller:
            return None

        if capture:
            self.controller.post_screencap().wait()
        im = self.controller.cached_image
        if im is None:
            return None

        return cvmat_to_image(im)

    @asyncify
    def click(self, x, y) -> bool:
        if not self.controller:
            return False

        return self.controller.post_click(x, y).wait().succeeded

    @asyncify
    def get_reco_detail(self, reco_id: int) -> Optional[RecognitionDetail]:
        if not self.tasker:
            return None

        return self.tasker.get_recognition_detail(reco_id)

    @asyncify
    def clear_cache(self) -> bool:
        if not self.tasker:
            return False

        return self.tasker.clear_cache()


# class Screenshotter(threading.Thread):
class Screenshotter:
    def __init__(self, screencap_func: Callable):
        super().__init__()
        self.source = None
        self.screencap_func = screencap_func
        # self.active = False

    def __del__(self):
        self.source = None
        # self.active = False

    async def refresh(self, capture: bool = True):
        im = await self.screencap_func(capture)
        if not im:
            return

        self.source = im

    # def run(self):
    #     while self.active:
    #         self.refresh()
    #         time.sleep(0)

    # def start(self):
    #     self.active = True
    #     super().start()

    # def stop(self):
    #     self.active = False


maafw = MaaFW()
