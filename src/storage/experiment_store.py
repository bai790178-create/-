import json
import os
from datetime import datetime


class ExperimentStore(object):
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.current_dir = None
        if not os.path.exists(self.root_dir):
            os.makedirs(self.root_dir)

    def create_experiment(self, params):
        base_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_dir = os.path.join(self.root_dir, base_name)
        suffix = 1
        while os.path.exists(self.current_dir):
            suffix += 1
            self.current_dir = os.path.join(self.root_dir, "{}_{}".format(base_name, suffix))
        os.makedirs(self.current_dir)
        self._write_json("params.json", params)
        self.append_log("实验记录创建。")
        return self.current_dir

    def save_image(self, name, frame):
        if self.current_dir is None:
            raise RuntimeError("请先创建实验记录。")
        path = os.path.join(self.current_dir, name)
        try:
            import cv2

            cv2.imencode(os.path.splitext(name)[1] or ".png", frame)[1].tofile(path)
        except Exception:
            raise RuntimeError("保存图片需要 OpenCV。")
        return path

    def save_analysis(self, result):
        if self.current_dir is None:
            raise RuntimeError("请先创建实验记录。")
        data = result.to_dict() if hasattr(result, "to_dict") else result
        self._write_json("analysis.json", data)
        return os.path.join(self.current_dir, "analysis.json")

    def append_log(self, message):
        if self.current_dir is None:
            return
        path = os.path.join(self.current_dir, "log.txt")
        line = "[{}] {}\n".format(datetime.now().strftime("%H:%M:%S"), message)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)

    def _write_json(self, filename, data):
        path = os.path.join(self.current_dir, filename)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
