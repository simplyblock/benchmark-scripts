#!/usr/bin/env python

import os
import re
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


def _parse_unit(unit: str, mode: str = 'si/iec', strict: bool = True) -> tuple[int, int]:
    """Parse the given unit, returning the associated base and exponent

    Mode can be either 'si/iec' to parse decimal (SI) and binary (IEC) units, or
    'jedec' for binary only units. If `strict`, parsing will be case-sensitive and
    expect the 'B' suffix.
    """
    regexes = {
        'si/iec': r'^((?P<prefix>[kKMGTPEZ])(?P<binary>i)?)?' + ('B$' if strict else 'B?$'),
        'jedec': r'^(?P<prefix>[KMGTPEZ])?' + ('B$' if strict else 'B?$'),
    }

    m = re.match(regexes[mode], unit, flags=re.IGNORECASE if not strict else 0)
    if m is None:
        raise ValueError("Invalid unit")

    binary = (mode == 'jedec') or (m.group('binary') is not None)
    prefix = m.group('prefix') or ''

    if strict and (binary and (prefix == 'k')) or ((not binary) and (prefix == 'K')):
        raise ValueError("Invalid unit")

    exponent_multipliers = ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z']
    return (
        2 if binary else 10,
        (10 if binary else 3) * exponent_multipliers.index(prefix.upper())
    )


def parse_size(size_string: str, mode: str = 'si/iec', unit: str = '', strict: bool = False) -> int:
    """Parse the given data size

    If passed and not explicitly given, 'unit' will be assumed.
    Mode can be either 'si/iec' to parse decimal (SI) and binary (IEC) units, or
    'jedec' for binary only units. If `strict`, parsing will be case-sensitive and
    expect the 'B' suffix.
    """
    if not unit:
        try:
            x = int(size_string)
            return x
        except Exception:
            pass

    try:
        m = re.match(r'^(?P<size_in_unit>\d+) ?(?P<unit>\w+)?$', size_string.strip())
        if m is None:
            raise ValueError(f"Invalid size: {size_string}")

        size_in_unit = int(m.group('size_in_unit'))
        unit = m.group('unit') if m.group('unit') else unit
        base, exponent = _parse_unit(unit, mode, strict=strict)
        return size_in_unit * (base ** exponent)
    except ValueError:
        return -1


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


def create_data_file(id: int, file_size: int):
    count = file_size / 1024**2
    working_directory = os.getcwd()
    data_file = os.path.join(working_directory, f"fio-benchmark-data-{id}")
    subprocess.check_output(
        ["dd", "if=/dev/urandom", f"of={data_file}", "bs=1M", f"count={count}"],
        stderr=subprocess.STDOUT,
    )


def run_workloads(worker_dir: str, loops: int, benchmarks: list[Benchmark]):
    file_size = parse_size(get_data_file_size())
    print("Laying out data files...", end='', flush=True)
    worker = benchmarks[0].worker
    for i in range(0, worker):
        print(f"{i + 1}", end='', flush=True)
        create_data_file(i, file_size)
        print("...", end='', flush=True)
    print(" done.")

    for benchmark in benchmarks:
        run_single_workload(worker_dir, loops, benchmark)


def estimated_time_per_benchmark() -> int:
    config = configparser.ConfigParser(allow_no_value=True)
    filepath = os.path.join(script_path, "workloads/global")
    config.read_string("[global]\n" + Path(filepath).read_text())

    runtime = config.getint("global", "runtime") if config.has_option("global", "runtime") else 0
    rampup = config.getint("global", "ramp_time") if config.has_option("global", "ramp_time") else 0
    return rampup + runtime


def get_data_file_size() -> str:
    config = configparser.ConfigParser(allow_no_value=True)
    filepath = os.path.join(script_path, "workloads/global")
    config.read_string("[global]\n" + Path(filepath).read_text())

    return config.getint("global", "size") if config.has_option("global", "size") else "1G"


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
