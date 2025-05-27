import csv
import math
import os
import sys

import matplotlib.pyplot as plt
import numpy as np

class Row:
    client_id: int
    transaction_no: int
    time: any
    script_no: int
    time_epoch: int
    time_us: int
    schedule_lag: int
    retries: int

    def __init__(self, row: str):
        tokens = row.split(" ")

        self.client_id = int(tokens[0])
        self.transaction_no = int(tokens[1])
        self.time = int(tokens[2]) if tokens[2].isnumeric() else tokens[2]
        self.script_no = int(tokens[3])
        self.time_epoch = int(tokens[4])
        self.time_us = int(tokens[5])

        if len(tokens) == 8:
            self.schedule_lag = int(tokens[6])
            self.retries = int(tokens[7])


def collect():
    collected: list[str] = []
    for dirpath, dirs, files in os.walk("pgbench-results/"):
        for file in files:
            if file.startswith("pgbench--1--select--200--20"):
                collected.append(os.path.join("pgbench-results", file))

    aggregation_items: dict[int, list[Row]] = {}
    for file in collected:
        print(f"Reading {file}...")
        with open(file, "r") as f:
            for line in f:
                row = Row(line)
                if not row.time_epoch in aggregation_items:
                    aggregation_items[row.time_epoch] = []
                aggregation_items[row.time_epoch].append(row)

    print("Writing TPS...")
    with open(os.path.join("pgbench-results", "tps.csv"), "w") as f:
        f.write("timestamp,tps\n")
        for time_epoch in aggregation_items:
            tps = len(aggregation_items[time_epoch])
            f.write(f"{time_epoch},{tps}\n")

    histogram_items_count = 0
    histogram_items_max = -1
    histogram_items_min = sys.maxsize

    latencies: dict[int, int] = {}
    pure_latencies: list[int] = []
    print("Collecting latencies...")
    for time_epoch in aggregation_items:
        latency = 0
        histogram_items_count += len(aggregation_items[time_epoch])
        for row in aggregation_items[time_epoch]:
            if row.time_us > histogram_items_max:
                histogram_items_max = row.time_us
            if histogram_items_min > row.time_us > 0:
                histogram_items_min = row.time_us
            latency += row.time
            pure_latencies.append(row.time_epoch)
        latencies[time_epoch] = latency // len(aggregation_items[time_epoch])

    print("Writing latencies...")
    with open("latencies.csv", "w") as f:
        f.write(f"Timestamp,Latency\n")
        timestamps = list(latencies.keys())
        timestamps.sort()
        for timestamp in timestamps:
            latency = latencies[timestamp]
            f.write(f"{timestamp},{latency}\n")

    print("Collecting latencies histogram...")
    latencies_histogram: dict[float, int] = {}
    num_bins = math.ceil(math.sqrt(histogram_items_count))
    latencies_np = np.array(pure_latencies)
    q1 = np.percentile(latencies_np, 25)
    q2 = np.percentile(latencies_np, 75)
    iqr = q2 - q1
    bin_width_new = 2 * iqr / histogram_items_count ** 1/3
    print(bin_width_new)
    bin_width = (histogram_items_max - histogram_items_min) / num_bins + 1
    bins: list[int] = [0] * num_bins
    for index in range(0, num_bins):
        bin_start = index * bin_width + histogram_items_min
        bins[index] = bin_start
        latencies_histogram[bin_start] = 0

    for latency in pure_latencies:
            bin_index = int(row.time_us // bin_width)
            latencies_histogram[bins[bin_index]] += 1

    with open("latencies-histogram.csv", "w") as f:
        f.write(f"Latency,Count\n")
        keys = list(latencies_histogram.keys())
        keys.sort()
        for latency in keys:
            count = latencies_histogram[latency]
            f.write(f"{latency},{count}\n")


collect()
