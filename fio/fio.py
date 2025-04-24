#!/usr/bin/env python


import argparse
import os
from fiobench.benchmark import Benchmark


script_path = os.path.dirname(os.path.realpath(__file__))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        add_help=False,
        prog="fio-runner",
        description="A tool to orchestrate fio workloads with multiple workers."
    )
    command_parser = parser.add_subparsers(dest="command")

    benchmark_parser = command_parser.add_parser("run", help="Run a benchmark definition")
    benchmark_parser.add_argument("--benchmark-file", required=True, type=str)
    benchmark_parser.add_argument("--worker-dir", required=True, type=str)
    benchmark_parser.add_argument("--workload-dir", required=False, type=str)

    namespace = parser.parse_args()

    if namespace.command == "run":
        worker_dir = namespace.worker_dir
        benchmark_file = namespace.benchmark_file
        workload_dir = namespace.workload_dir if namespace.workload_dir else script_path
        benchmark = Benchmark(benchmark_file, worker_dir, workload_dir)
        benchmark.run_all_workloads()
    else:
        parser.print_help()
