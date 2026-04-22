#!/usr/bin/env python


import argparse
import os
from fiobench.benchmark import Benchmark
from fiobench.analyze import Analyzer


script_path = os.path.dirname(os.path.realpath(__file__))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        add_help=False,
        prog="fio-runner",
        description="A tool to orchestrate fio workloads with multiple workers."
    )
    command_parser = parser.add_subparsers(dest="command")

    benchmark_parser = command_parser.add_parser("run", help="Run a benchmark definition")
    benchmark_parser.add_argument("--benchmark-file", "-b", required=True, type=str, dest="benchmark_file")
    benchmark_parser.add_argument("--worker-dir", "-w", required=True, type=str, dest="worker_dir")
    benchmark_parser.add_argument("--workload-dir" "-wl", required=False, type=str, dest="workload_dir")
    benchmark_parser.add_argument(
        "--skip-precreation",
        action="store_true",
        dest="skip_precreation",
        help="Skip fio data file precreation before running each workload.",
    )

    analytics_parser = command_parser.add_parser("analyze", help="Analyze benchmark results")
    analytics_parser.add_argument("--benchmark-result-dir", "-b", required=True, type=str, dest="benchmark_result_dir")

    namespace = parser.parse_args()

    if namespace.command == "run":
        worker_dir = namespace.worker_dir
        benchmark_file = namespace.benchmark_file
        workload_dir = namespace.workload_dir if namespace.workload_dir else script_path
        benchmark = Benchmark(
            benchmark_file,
            worker_dir,
            workload_dir,
            skip_precreation=namespace.skip_precreation,
        )
        benchmark.run_all_workloads()
    elif namespace.command == "analyze":
        benchmark_result_dir = namespace.benchmark_result_dir
        analyzer = Analyzer(benchmark_result_dir)
        analyzer.analyze()
    else:
        parser.print_help()
