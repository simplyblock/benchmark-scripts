import configparser
import os
import subprocess
from pathlib import Path
import yaml

from .utils import parse_size


class Workload:
    io_depth: int
    worker: int
    blocksize: str
    workload_name: str
    workload_file: str

    def __init__(self, io_depth: int, worker: int, blocksize: str, workload_name: str, workload_file: str):
        self.io_depth = io_depth
        self.worker = worker
        self.blocksize = blocksize
        self.workload_name = workload_name
        self.workload_file = workload_file


class Benchmark:
    def __init__(self, benchmark_file: str, worker_dir: str, workload_dir: str):
        self.benchmark_file = benchmark_file
        self.worker_dir = worker_dir
        self.workload_dir = workload_dir

    def prepare_fio_files(self, size: str, blocksize: str, worker: int):
        fio_args = [
            "fio",
            f"--blocksize={blocksize}",
            f"--numjobs={worker}",
            "--readwrite=write",
            "--direct=1",
            f"--size={size}",
            "--filename_format=fio-benchmark-data-$jobnum",
            "--name=prepare"
        ]
        subprocess.check_call(fio_args)
        print()

    def run_fio(self, loops: int, workload: Workload):
        for i in range(loops):
            print(f"Running workload %s loop #%d" % (workload.workload_name, i + 1))

            print(" - Dropping Caches...")
            subprocess.run("sync").check_returncode()
            Path("/proc/sys/vm/drop_caches").write_text("3")

            print(" - Staring fio...")
            report_file = f"{workload.workload_name}-{workload.io_depth}-{workload.worker}-{workload.blocksize}-{i + 1}.json"
            output_file = os.path.join(self.worker_dir, report_file)
            job_file = os.path.join(self.worker_dir, "job.fio")
            fio_args = [
                "fio",
                job_file,
                f"--output={output_file}",
                "--output-format=json",
                f"--blocksize={workload.blocksize}",
                f"--iodepth={workload.io_depth}",
                f"--numjobs={workload.worker}",
            ]
            subprocess.check_call(fio_args)
            print()

    def build_job_file(self, workload_file: str):
        content = Path(workload_file).read_text()
        content += Path(f"{self.workload_dir}/workloads/global").read_text()
        Path(f"{self.worker_dir}/job.fio").write_text(content)

    def run_single_workload(self, loops: int, workload: Workload):
        print(f"Preparing benchmark data files for workload '{workload.workload_name}' with " +
              f"iodepth: {workload.io_depth}, workers: {workload.worker}, blocksize: {workload.blocksize}...")
        self.prepare_fio_files(self.get_data_file_size(), workload.blocksize, workload.worker)

        print(f"Running benchmark for workload '{workload.workload_name}' with " +
              f"iodepth: {workload.io_depth}, workers: {workload.worker}, blocksize: {workload.blocksize}...")

        self.build_job_file(workload.workload_file)
        self.run_fio(loops, workload)

    def old_data_file_layout(self, workloads: list[Workload]):
        file_size = parse_size(self.get_data_file_size())
        print("Laying out data files...", end='', flush=True)
        worker = workloads[0].worker
        for i in range(0, worker):
            print(f"{i + 1}... ", end='', flush=True)
            self.create_data_file(i, file_size)
        print(" done.")

    def create_data_file(self, id: int, file_size: int):
        count = int(file_size / 1000 ** 2)
        working_directory = os.getcwd()
        data_file = os.path.join(working_directory, f"fio-benchmark-data-{id}")
        subprocess.check_output(
            ["dd", "if=/dev/urandom", f"of={data_file}", "bs=1M", f"count={count}"],
            stderr=subprocess.STDOUT,
        )

    def run_workloads(self, loops: int, workloads: list[Workload]):
        for workload in workloads:
            self.run_single_workload(loops, workload)

    def estimated_time_per_benchmark(self) -> int:
        config = configparser.ConfigParser(allow_no_value=True)
        filepath = os.path.join(self.workload_dir, "workloads/global")
        config.read_string("[global]\n" + Path(filepath).read_text())

        runtime = config.getint("global", "runtime") if config.has_option("global", "runtime") else 0
        rampup = config.getint("global", "ramp_time") if config.has_option("global", "ramp_time") else 0
        return rampup + runtime

    def get_data_file_size(self) -> str:
        config = configparser.ConfigParser(allow_no_value=True)
        filepath = os.path.join(self.workload_dir, "workloads/global")
        config.read_string("[global]\n" + Path(filepath).read_text())

        return config.get("global", "size") if config.has_option("global", "size") else "1G"

    def run_all_workloads(self):
        benchmark = yaml.safe_load(Path(self.benchmark_file).read_text())
        loops = int(benchmark["benchmark"]["loops"])
        blocksizes = benchmark["benchmark"]["blocksizes"]
        workers = list(map(int, benchmark["benchmark"]["workers"]))
        io_depths = list(map(int, benchmark["benchmark"]["iodepths"]))
        workloads = benchmark["benchmark"]["workloads"]

        print("Planned benchmarks:")

        workload_definitions = []
        for workload in workloads:
            for io_depth in io_depths:
                for blocksize in blocksizes:
                    for worker in workers:
                        print(
                            f" - Workload '{workload}' with iodepth: {io_depth}, "
                            f"blocksize: {blocksize}, worker: {worker}"
                        )
                        workload_file = str(os.path.join(self.workload_dir, "workloads", workload))
                        workload_definitions.append(
                            Workload(io_depth, worker, blocksize, workload, workload_file)
                        )

        seconds = len(workload_definitions) * loops * self.estimated_time_per_benchmark()
        minutes = seconds // 60
        seconds %= 60
        hours = minutes // 60
        minutes = minutes % 60
        days = hours // 24
        hours = hours % 24

        print(f"Estimated runtime: > {days} days {hours} hours {minutes} minutes {seconds} seconds")

        self.run_workloads(loops, workload_definitions)
        print("All benchmarks complete.")


def _get_workload_name(workload_file: str) -> str:
    config = configparser.ConfigParser()
    config.read_string(Path(workload_file).read_text())

    for section in config.sections():
        return config[section]["name"]

    raise ValueError("No workload name in file found")


def _get_workload_description(workload_file: str) -> str:
    config = configparser.ConfigParser()
    config.read_string(Path(workload_file).read_text())

    for section in config.sections():
        return config[section]["description"]

    raise ValueError("No workload name in file found")
