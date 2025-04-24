import configparser
import os
from pathlib import Path


def list_available_workloads(workload_dir: str):
    print("Available workloads:")
    for dirpath, dirnames, filenames in os.walk(os.path.join(workload_dir, "workloads")):
        for filename in filenames:
            if filename.endswith(".fio"):
                config = configparser.ConfigParser()
                filepath = os.path.join(dirpath, filename)
                config.read_string(Path(filepath).read_text())

                for section in config.sections():
                    print(" - Workload:", config[section]["name"])
                    print("            ", config[section]["description"])
