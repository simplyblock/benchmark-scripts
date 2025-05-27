import os.path

from fio_plot.fiolib import getdata
from fio_plot.fiolib.defaultsettings import get_default_settings


class Plotter:
    def __init__(self, benchmark_result_dir: str, graph_type: str):
        self.graph_type = graph_type
        self.settings = get_default_settings()
        self.settings["input_directory"] = [benchmark_result_dir]
        self.settings["output_filename"] = os.path.join(benchmark_result_dir, "plot.png")
        self.settings["iodepth"] = [64, 128]
        self.settings["numjobs"] = [20]
        self.settings["rw"] = "randread"
        self.settings["source"] = None
        self.settings["graphtype"] = graph_type
        self.settings["group_bars"] = True
        self.settings["title"] = "Test"

    def plot(self):
        routing_dict = getdata.get_routing_dict()
        settings = getdata.configure_default_settings(self.settings, routing_dict, self.graph_type)
        data = routing_dict[self.graph_type]["get_data"](settings)
        routing_dict[self.graph_type]["function"](settings, data)
