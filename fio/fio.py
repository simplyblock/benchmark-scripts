#!/usr/bin/env python

import os
import sys
from pathlib import Path
import configparser
import subprocess

import yaml

script_path = os.path.dirname(os.path.realpath(__file__))


class Benchmark:
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


def list_available_workloads():
    print("Available workloads:")
    for dirpath, dirnames, filenames in os.walk(os.path.join(script_path, "workloads")):
        for filename in filenames:
            if filename.endswith(".fio"):
                config = configparser.ConfigParser()
                filepath = os.path.join(dirpath, filename)
                config.read_string(Path(filepath).read_text())

                for section in config.sections():
                    print(" - Workload:", config[section]["name"])
                    print("            ", config[section]["description"])


def run_fio(worker_dir: str, workload: str, loops: int, io_depth: int, workers: int, blocksize: str):
    for i in range(loops):
        print(f"Running workload %s loop #%d" % (workload, i + 1))

        print(" - Dropping Caches...")
        subprocess.run("sync").check_returncode()
        Path("/proc/sys/vm/drop_caches").write_text("3")

        print(" - Staring fio...")
        output_file = os.path.join(worker_dir, f"{workload}-{io_depth}-{workers}-{blocksize}-{i + 1}.json")
        job_file = os.path.join(worker_dir, "job.fio")
        fio_args = [
            "fio",
            job_file,
            f"--output={output_file}",
            "--output-format=json",
            f"--blocksize={blocksize}",
            f"--iodepth={io_depth}",
            f"--numjobs={workers}",
        ]
        subprocess.check_call(fio_args)
        print()


def get_workload_name(workload_file: str) -> str:
    config = configparser.ConfigParser()
    config.read_string(Path(workload_file).read_text())

    for section in config.sections():
        return config[section]["name"]

    raise ValueError("No workload name in file found")


def get_workload_description(workload_file: str) -> str:
    config = configparser.ConfigParser()
    config.read_string(Path(workload_file).read_text())

    for section in config.sections():
        return config[section]["description"]

    raise ValueError("No workload name in file found")


def build_job_file(worker_dir: str, workload_file: str):
    content = Path(workload_file).read_text()
    content += Path(f"{script_path}/workloads/global").read_text()
    Path(f"{worker_dir}/job.fio").write_text(content)


def run_single_workload(worker_dir: str, loops: int, benchmark: Benchmark):
    workload_name = get_workload_name(benchmark.workload_file)

    print(f"Running benchmark for workload '{workload_name}' with " +
          f"iodepth: {benchmark.io_depth}, workers: {benchmark.worker}, blocksize: {benchmark.blocksize}...")

    build_job_file(worker_dir, benchmark.workload_file)

    run_fio(worker_dir, workload_name, loops, benchmark.io_depth, benchmark.worker, benchmark.blocksize)


def run_workloads(worker_dir: str, loops: int, benchmarks: list[Benchmark]):
    for benchmark in benchmarks:
        run_single_workload(worker_dir, loops, benchmark)


def estimated_time_per_benchmark():
    config = configparser.ConfigParser(allow_no_value=True)
    filepath = os.path.join(script_path, "workloads/global")
    config.read_string("[global]\n" + Path(filepath).read_text())

    runtime = config.getint("global", "runtime") if config.has_option("global", "runtime") else 0
    rampup = config.getint("global", "ramp_time") if config.has_option("global", "ramp_time") else 0
    return rampup + runtime


def run_all_workloads():
    worker_dir = os.path.abspath(sys.argv[1])
    benchmark = yaml.safe_load(Path(sys.argv[2]).read_text())
    loops = int(benchmark["benchmark"]["loops"])
    blocksizes = benchmark["benchmark"]["blocksizes"]
    workers = list(map(int, benchmark["benchmark"]["workers"]))
    io_depths = list(map(int, benchmark["benchmark"]["iodepths"]))
    workloads = benchmark["benchmark"]["workloads"]
    workload_files = []
    for workload in workloads:
        workload_files.append(str(os.path.join(script_path, "workloads", workload)))

    print("Planned benchmarks:")

    benchmarks = []
    for workload in workloads:
        for io_depth in io_depths:
            for blocksize in blocksizes:
                for worker in workers:
                    print(
                        f" - Workload '{workload}' with iodepth: {io_depth}, "
                        f"blocksize: {blocksize}, worker: {worker}"
                    )
                    benchmarks.append(
                        Benchmark(
                            io_depth, worker, blocksize, workload,
                            str(os.path.join(script_path, "workloads", workload))
                        )
                    )

    seconds = len(benchmarks) * loops * estimated_time_per_benchmark()
    minutes = seconds // 60
    seconds %= 60
    hours = minutes // 60
    minutes = minutes % 60
    days = hours // 24
    hours = hours % 24

    print(f"Estimated runtime: {days} days {hours} hours {minutes} minutes {seconds} seconds")

    run_workloads(worker_dir, loops, benchmarks)
    print("All benchmarks complete.")


run_all_workloads()
