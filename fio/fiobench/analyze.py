import json
import os
from pathlib import Path

PERCENTILES = [50.0, 90.0, 95.0, 99.0, 99.9, 99.99]


def _pct_key(value: float) -> str:
    return f"{value:.6f}"


def _pct_label(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value).replace(".", "_")


def _ns_to_us(value):
    if value is None:
        return None
    return float(value) / 1000.0


def _avg_or_none(values):
    if not values:
        return None
    return sum(values) / len(values)


def _fmt_or_empty(value, digits: int = 2) -> str:
    if value is None:
        return ""
    return str(round(value, digits))


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Analyzer:
    def __init__(self, benchmark_result_dir: str):
        self.benchmark_result_dir = benchmark_result_dir

    def analyze(self):
        benchmark_loop_files = {}
        for dirpath, dirnames, filenames in os.walk(self.benchmark_result_dir):
            for filename in filenames:
                if not filename.endswith(".json"):
                    continue

                groupby = filename[:len(filename) - 6]
                if not groupby in benchmark_loop_files:
                    benchmark_loop_files[groupby] = []

                files = benchmark_loop_files[groupby]
                path = os.path.join(dirpath, filename)

                files.append(path)

        with open(os.path.join(self.benchmark_result_dir, "summary.csv"), "w") as f:
            latency_header = ",".join([
                "avg_read_lat_us",
                "avg_write_lat_us",
                *[f"avg_read_p{_pct_label(p)}_lat_us" for p in PERCENTILES],
                *[f"avg_write_p{_pct_label(p)}_lat_us" for p in PERCENTILES],
            ])
            f.write(
                "name,blocksize,iodepth,workers,"
                "avg_read_iops,min_read_iops,max_read_iops,stddev_read_iops,"
                "avg_write_iops,min_write_iops,max_write_iops,stddev_write_iops,"
                "avg_total_iops,min_total_iops,max_total_iops,stddev_total_iops,"
                "avg_read_throughput,avg_write_throughput,"
                f"{latency_header}\n"
            )
            for benchmark_group in benchmark_loop_files.values():
                read_iops_mean = []
                read_iops_min = []
                read_iops_max = []
                read_iops_stddev = []
                write_iops_mean = []
                write_iops_min = []
                write_iops_max = []
                write_iops_stddev = []
                total_iops_mean = []
                total_iops_min = []
                total_iops_max = []
                total_iops_stddev = []
                read_throughputs = []
                write_throughputs = []
                read_lat_us = []
                write_lat_us = []
                read_pct_us = {p: [] for p in PERCENTILES}
                write_pct_us = {p: [] for p in PERCENTILES}

                benchmark_name = ""
                num_jobs = 0
                blocksize = "0K"
                iodepth = 0
                for file in benchmark_group:
                    content = json.loads(Path(file).read_text())

                    benchmark_name = content['jobs'][0]['jobname']
                    num_jobs = content['global options']['numjobs']
                    blocksize = content['global options']['bs']
                    iodepth = content['global options']['iodepth']

                    file_read_mean = 0.0
                    file_read_min = 0.0
                    file_read_max = 0.0
                    file_read_stddev = 0.0
                    file_write_mean = 0.0
                    file_write_min = 0.0
                    file_write_max = 0.0
                    file_write_stddev = 0.0
                    job_read_throughputs = []
                    job_write_throughputs = []
                    job_read_lat_us = []
                    job_write_lat_us = []
                    job_read_pct_us = {p: [] for p in PERCENTILES}
                    job_write_pct_us = {p: [] for p in PERCENTILES}
                    for job in content['jobs']:
                        read_stats = job.get('read', {})
                        write_stats = job.get('write', {})
                        file_read_mean += _safe_float(read_stats.get('iops_mean'))
                        file_read_min += _safe_float(read_stats.get('iops_min'))
                        file_read_max += _safe_float(read_stats.get('iops_max'))
                        file_read_stddev += _safe_float(read_stats.get('iops_stddev'))
                        file_write_mean += _safe_float(write_stats.get('iops_mean'))
                        file_write_min += _safe_float(write_stats.get('iops_min'))
                        file_write_max += _safe_float(write_stats.get('iops_max'))
                        file_write_stddev += _safe_float(write_stats.get('iops_stddev'))
                        job_read_throughputs.append(_safe_float(read_stats.get('bw_bytes')))
                        job_write_throughputs.append(_safe_float(write_stats.get('bw_bytes')))

                        read_clat = read_stats.get('clat_ns', {})
                        write_clat = write_stats.get('clat_ns', {})
                        read_lat_value = _ns_to_us(read_clat.get('mean'))
                        write_lat_value = _ns_to_us(write_clat.get('mean'))
                        if read_lat_value is not None:
                            job_read_lat_us.append(read_lat_value)
                        if write_lat_value is not None:
                            job_write_lat_us.append(write_lat_value)

                        read_pct_map = read_clat.get('percentile', {})
                        write_pct_map = write_clat.get('percentile', {})
                        for pct in PERCENTILES:
                            read_pct_value = _ns_to_us(read_pct_map.get(_pct_key(pct)))
                            write_pct_value = _ns_to_us(write_pct_map.get(_pct_key(pct)))
                            if read_pct_value is not None:
                                job_read_pct_us[pct].append(read_pct_value)
                            if write_pct_value is not None:
                                job_write_pct_us[pct].append(write_pct_value)

                    read_iops_mean.append(file_read_mean)
                    read_iops_min.append(file_read_min)
                    read_iops_max.append(file_read_max)
                    read_iops_stddev.append(file_read_stddev)
                    write_iops_mean.append(file_write_mean)
                    write_iops_min.append(file_write_min)
                    write_iops_max.append(file_write_max)
                    write_iops_stddev.append(file_write_stddev)
                    total_iops_mean.append(file_read_mean + file_write_mean)
                    total_iops_min.append(file_read_min + file_write_min)
                    total_iops_max.append(file_read_max + file_write_max)
                    total_iops_stddev.append(file_read_stddev + file_write_stddev)
                    read_throughputs.append(sum(job_read_throughputs))
                    write_throughputs.append(sum(job_write_throughputs))
                    file_read_lat = _avg_or_none(job_read_lat_us)
                    file_write_lat = _avg_or_none(job_write_lat_us)
                    if file_read_lat is not None:
                        read_lat_us.append(file_read_lat)
                    if file_write_lat is not None:
                        write_lat_us.append(file_write_lat)
                    for pct in PERCENTILES:
                        file_read_pct = _avg_or_none(job_read_pct_us[pct])
                        file_write_pct = _avg_or_none(job_write_pct_us[pct])
                        if file_read_pct is not None:
                            read_pct_us[pct].append(file_read_pct)
                        if file_write_pct is not None:
                            write_pct_us[pct].append(file_write_pct)

                avg_read_iops = round(sum(read_iops_mean) / len(read_iops_mean), 2)
                min_read_iops = round(min(read_iops_min), 2)
                max_read_iops = round(max(read_iops_max), 2)
                stddev_read_iops = round(sum(read_iops_stddev) / len(read_iops_stddev), 2)

                avg_write_iops = round(sum(write_iops_mean) / len(write_iops_mean), 2)
                min_write_iops = round(min(write_iops_min), 2)
                max_write_iops = round(max(write_iops_max), 2)
                stddev_write_iops = round(sum(write_iops_stddev) / len(write_iops_stddev), 2)

                avg_total_iops = round(sum(total_iops_mean) / len(total_iops_mean), 2)
                min_total_iops = round(min(total_iops_min), 2)
                max_total_iops = round(max(total_iops_max), 2)
                stddev_total_iops = round(sum(total_iops_stddev) / len(total_iops_stddev), 2)

                avg_read_throughput = round(sum(read_throughputs) / len(read_throughputs) / 1000 / 1000 * 8, 2)
                avg_write_throughput = round(sum(write_throughputs) / len(write_throughputs) / 1000 / 1000 * 8, 2)
                avg_read_lat = _avg_or_none(read_lat_us)
                avg_write_lat = _avg_or_none(write_lat_us)
                avg_read_pct = {pct: _avg_or_none(read_pct_us[pct]) for pct in PERCENTILES}
                avg_write_pct = {pct: _avg_or_none(write_pct_us[pct]) for pct in PERCENTILES}
                latency_values = [
                    _fmt_or_empty(avg_read_lat),
                    _fmt_or_empty(avg_write_lat),
                    *[_fmt_or_empty(avg_read_pct[pct]) for pct in PERCENTILES],
                    *[_fmt_or_empty(avg_write_pct[pct]) for pct in PERCENTILES],
                ]

                f.write(
                    f"{benchmark_name},{blocksize},{iodepth},{num_jobs},"
                    f"{avg_read_iops},{min_read_iops},{max_read_iops},{stddev_read_iops},"
                    f"{avg_write_iops},{min_write_iops},{max_write_iops},{stddev_write_iops},"
                    f"{avg_total_iops},{min_total_iops},{max_total_iops},{stddev_total_iops},"
                    f"{avg_read_throughput},{avg_write_throughput},"
                    f"{','.join(latency_values)}\n"
                )
