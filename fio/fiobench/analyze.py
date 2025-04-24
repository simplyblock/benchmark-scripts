import json
import sys
import os
from pathlib import Path

benchmark_loop_files = {}
for dirpath, dirnames, filenames in os.walk(sys.argv[1]):
    for filename in filenames:
        groupby = filename[:len(filename)-6]

        if not groupby in benchmark_loop_files:
            benchmark_loop_files[groupby] = []

        files = benchmark_loop_files[groupby]
        path = os.path.join(dirpath, filename)

        files.append(path)

print ('name,blocksize,iodepth,workers,read_iops,write_iops,total_iops,avg_read_throughput,avg_write_throughput')
for benchmark_group in benchmark_loop_files.values():
    read_iops=[]
    write_iops=[]
    read_throughputs = []
    write_throughputs = []

    num_of_loops = -1
    benchmark_name = ""
    num_jobs = 0
    blocksize = "0K"
    iodepth = 0
    for file in benchmark_group:
        content = json.loads(Path(file).read_text())

        benchmark_name = content['jobs'][1]['jobname']
        num_jobs = content['global options']['numjobs']
        blocksize = content['global options']['bs']
        iodepth = content['global options']['iodepth']

        num_of_loops = len(content['jobs'])

        job_read_iops = []
        job_write_iops = []
        job_read_throughputs = []
        job_write_throughputs = []
        for job in content['jobs']:
            job_read_iops.append(job['read']['iops_mean'])
            job_write_iops.append(job['write']['iops_mean'])
            job_read_throughputs.append(job['read']['bw_bytes'])
            job_write_throughputs.append(job['write']['bw_bytes'])

        read_iops.append(sum(job_read_iops))
        write_iops.append(sum(job_write_iops))
        read_throughputs.append(sum(job_read_throughputs))
        write_throughputs.append(sum(job_write_throughputs))

    avg_read_iops = round(sum(read_iops) / len(read_iops), 2)
    avg_write_iops = round(sum(write_iops) / len(write_iops), 2)
    avg_total_iops = round(avg_write_iops + avg_read_iops, 2)
    avg_read_throughput = round(sum(read_throughputs) / len(read_throughputs) / 1000 / 1000 * 8, 2)
    avg_write_throughput = round(sum(write_throughputs) / len(write_throughputs) / 1000 / 1000 * 8, 2)

    print(f"{benchmark_name},{blocksize},{iodepth},{num_jobs},{avg_read_iops},{avg_write_iops},{avg_total_iops},{avg_read_throughput},{avg_write_throughput}")
