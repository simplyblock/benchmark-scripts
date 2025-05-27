import csv
import math
import os
import sys
from typing import Tuple

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
    for dirpath, dirs, files in os.walk("pgbench-results-2/"):
        for file in files:
            if file.startswith("pgbench--1--select--50--20"):
                collected.append(os.path.join("pgbench-results-2", file))

    aggregation_items: dict[int, Tuple[int, int]] = {}
    for file in collected:
        print(f"Reading {file}...")
        with open(file, "r") as f:
            for line in f:
                tokens = line.split(" ")
                time_us = int(tokens[5])
                time_epoch = int(tokens[4])
                if not time_epoch in aggregation_items:
                    aggregation_items[time_epoch] = (0, 0)
                val, count = aggregation_items[time_epoch]
                val += time_us
                count += 1
                aggregation_items[time_epoch] = (val, count)

    print("Writing TPS...")
    with open(os.path.join("pgbench-results-2", "tps.csv"), "w") as f:
        f.write("timestamp,tps\n")
        for time_epoch in aggregation_items:
            _, tps = aggregation_items[time_epoch]
            f.write(f"{time_epoch},{tps}\n")

    latencies: dict[int, int] = {}
    print("Collecting latencies...")
    for time_epoch in aggregation_items:
        latency, count= aggregation_items[time_epoch]
        latencies[time_epoch] = latency // count

    print("Writing latencies...")
    with open("latencies.csv", "w") as f:
        f.write(f"Timestamp,Latency\n")
        timestamps = list(latencies.keys())
        timestamps.sort()
        for timestamp in timestamps:
            latency = latencies[timestamp]
            f.write(f"{timestamp},{latency}\n")


collect()
