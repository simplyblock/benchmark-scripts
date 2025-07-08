#!/usr/bin/env bash
# This test is designed to run on GCP with storage nodes of instance type c4a-standard-64-lssd and
# clients of instance type c3-standard-88 and a VPC with jumbo frames enabled.

set -e

instance_ram_gb=352
accelerator_type="h100"

# c3-standard-88 can handle 15 accelerators with cosmoflow per host
cosmoflow_max_accelerators_per_host=15

# c3-standard-88 can handle 44 accelerators with resnet50 per host
resnet50_max_accelerators_per_host=44

# c3-standard-88 can handle 2 accelerators with unet3d per host
unet3d_max_accelerators_per_host=2

results_base_dir="${HOME}/.results"
data_dir="/media/benchmark"
base_args="--closed --exec-type=mpi --data-dir=${data_dir}"

benchmark_loops=5

function get_system_memory() {
  free --giga | grep 'Mem:' | awk '{print $2}'
}

function drop_caches() {
  ifs_bk="${IFS}" # store IFS
  IFS=","
  hosts_split=()
  read -a hosts_split <<< "${hosts}"
  IFS="${ifs_bk}" # reset IFS

  for host in ${hosts_split[@]}; do
    echo "  - Dropping for ${host}..."
    if [[ "$(ip addr | grep ${host})" == "" ]]; then
      ssh ${USER}@${host} -C "sudo sh -c '/usr/bin/echo 3 > /proc/sys/vm/drop_caches'"
    else
      sudo sh -c '/usr/bin/echo 3 > /proc/sys/vm/drop_caches'
    fi
  done
}

function get_num_of_training_files() {
  model="$1"
  num_of_hosts="$2"
  accelerators="$3"
  mlpstorage training datasize ${base_args} --model=${model} --hosts=127.0.0.1 --max-accelerators=${accelerators} --client-host-memory-in-gb=$(get_system_memory) --num-client-hosts=${num_of_hosts} --accelerator-type=${accelerator_type} --results-dir="${results_base_dir}" 2>&1 | tee -a | grep num_files_train | sed 's/.*num_files_train=\([0-9]*\).*/\1/'
}

function get_data_size() {
  model="$1"
  num_of_hosts="$2"
  accelerators="$3"
  mlpstorage training datasize ${base_args} --model=${model} --hosts=127.0.0.1 --max-accelerators=${accelerators} --client-host-memory-in-gb=$(get_system_memory) --num-client-hosts=${num_of_hosts} --accelerator-type=${accelerator_type} --results-dir="${results_base_dir}" 2>&1 | grep 'Total disk space required for training' | sed 's/.*Total disk space required for training:\ \([0-9.]*\).*/\1/'
}

function get_checkpoint_data_size() {
  model="$1"
  num_of_hosts="$2"
  processes="$3"
  mlpstorage checkpointing datasize --hosts=127.0.0.1 --client-host-memory-in-gb=$(get_system_memory) --model=${model} --checkpoint-folder="${data_dir}" --num-processes=${processes} 2>&1 | grep 'Total GB required for all ranks' | sed 's/.*Total GB required for all ranks:\ \([0-9.]*\).*/\1/'
}

function run_benchmark_iteration() {
  iteration="$1"
  model="$2"
  num_of_hosts="$3"
  accelerators="$4"
  hosts="$5"
  results_dir="${results_base_dir}/${model}-${num_of_hosts}/run${iteration}"
  num_of_files="$(get_num_of_training_files ${model} ${num_of_hosts} ${accelerators})"
  total_accelerators=$( "$((${num_of_hosts} * ${accelerators}))")
  ram_size="$(get_system_memory)"
  echo "Info: Discovered ${ram_size} GB of system memory."
  echo "Info: ${num_of_files} files required for this benchmark."
  echo "Dropping caches..."
  drop_caches "$hosts"
  echo "Running benchmark iteration ${iteration} on hosts: ${hosts}..."
  mlpstorage training run ${base_args} --model=${model} --hosts="${hosts}" --client-host-memory-in-gb=${ram_size} --num-accelerators=${total_accelerators} --accelerator-type=${accelerator_type} --results-dir="${results_dir}" --params dataset.num_files_train=${num_of_files}
}

function gen_benchmark_data() {
  model="$1"
  num_of_hosts="$2"
  accelerators="$3"
  num_of_generators="$4"
  results_dir="${results_base_dir}/${model}-${num_of_hosts}"
  num_of_files="$(get_num_of_training_files ${model} ${num_of_hosts} ${accelerators})"
  data_size="$(get_data_size ${model} ${num_of_hosts} ${accelerators})"
  echo "Info: ${data_size} GB are required for this benchmark."
  echo "Info: ${num_of_files} files required for this benchmark."
  echo "Generating training data files for ${num_of_hosts}: ${num_of_files}..."
  mlpstorage training datagen ${base_args} --model=${model} --hosts=127.0.0.1 --num-processes=${num_of_generators} --results-dir="${results_dir}" --param dataset.num_files_train=${num_of_files}
}

function show_expected_data_size() {
  model="$1"
  num_of_hosts="$2"
  accelerators="$3"
    data_size="$(get_data_size ${model} ${num_of_hosts} ${accelerators})"
    echo "The expected data size is: ${data_size} GB"
}

function show_expected_checkpoint_data_size() {
  model="$1"
  num_of_hosts="$2"
  processes="$3"
    data_size="$(get_checkpoint_data_size ${model} ${num_of_hosts} ${processes})"
    echo "The expected data size is: ${data_size} GB"
}

function create_benchmark_report() {
  model="$1"
  num_of_hosts="$2"
  results_dir="${results_base_dir}/${model}-${num_of_hosts}"
  mlpstorage reports reportgen --results-dir="${results_dir}"
}

function resnet50() {
  action="$1"
  num_of_hosts="$2"
  hosts="$3"

  if [[ "$action" == "gen" ]]; then
    gen_benchmark_data resnet50 ${num_of_hosts} ${resnet50_max_accelerators_per_host} ${resnet50_max_accelerators_per_host}
  elif [[ "$action" == "size" ]]; then
    show_expected_data_size resnet50 ${num_of_hosts} ${resnet50_max_accelerators_per_host}
  elif [[ "$action" == "report" ]]; then
    create_benchmark_report resnet50 ${num_of_hosts}
  else
    for iteration in $(seq 1 ${benchmark_loops}); do
      run_benchmark_iteration ${iteration} resnet50 ${num_of_hosts} ${resnet50_max_accelerators_per_host} "${hosts}"
    done
  fi
}

function cosmoflow() {
  action="$1"
  num_of_hosts="$2"
  hosts="$3"

  if [[ "$action" == "gen" ]]; then
    gen_benchmark_data cosmoflow ${num_of_hosts} ${cosmoflow_max_accelerators_per_host} ${cosmoflow_max_accelerators_per_host}
  elif [[ "$action" == "size" ]]; then
    show_expected_data_size cosmoflow ${num_of_hosts} ${cosmoflow_max_accelerators_per_host}
  elif [[ "$action" == "report" ]]; then
    create_benchmark_report cosmoflow ${num_of_hosts}
  else
    for iteration in $(seq 1 ${benchmark_loops}); do
      run_benchmark_iteration ${iteration} cosmoflow ${num_of_hosts} ${cosmoflow_max_accelerators_per_host} "${hosts}"
    done
  fi
}

function unet3d() {
  action="$1"
  num_of_hosts="$2"
  hosts="$3"

  if [[ "$action" == "gen" ]]; then
    gen_benchmark_data unet3d ${num_of_hosts} ${unet3d_max_accelerators_per_host} 10
  elif [[ "$action" == "size" ]]; then
    show_expected_data_size unet3d ${num_of_hosts} ${unet3d_max_accelerators_per_host}
  elif [[ "$action" == "report" ]]; then
    create_benchmark_report unet3d ${num_of_hosts}
  else
    for iteration in $(seq 1 ${benchmark_loops}); do
      run_benchmark_iteration ${iteration} unet3d ${num_of_hosts} ${unet3d_max_accelerators_per_host} "${hosts}"
    done
  fi
}

function checkpoint() {
  action="$1"
  model="$2"
  num_of_hosts="$3"
  processes="$4"
  hosts="$5"

  if [[ "$action" == "gen" ]]; then
    echo "Checkpointing doesn't support the action gen."
    help
    exit 1
  elif [[ "$action" == "size" ]]; then
    show_expected_checkpoint_data_size "llama3-405b" ${num_of_hosts} 512
  else
    results_dir="${results_base_dir}/${model}-${num_of_hosts}"
    ram_size="$(get_system_memory)"
    echo "Info: Discovered ${ram_size} GB of system memory."
    echo "Writing checkpoints..."
    mlpstorage checkpointing run --client-host-memory-in-gb=${ram_size} --model=${model} --num-processes=${processes} --checkpoint-folder /media/benchmark --hosts=127.0.0.1 --num-checkpoints-write=10 --closed --results-dir="${results_dir}" --num-checkpoints-read=0
    echo "Dropping caches..."
    drop_caches "$hosts"
    echo "Restoring checkpoints..."
    mlpstorage checkpointing run --client-host-memory-in-gb=${ram_size} --model=${model} --num-processes=${processes} --checkpoint-folder /media/benchmark --hosts="${hosts}" --num-checkpoints-write=0 --closed --results-dir="${results_dir}" --num-checkpoints-read=10
  fi
}

function help() {
  echo "MLCommons Storge Benchmark Automation 2025"
  echo
  echo "Usage:"
  echo "storage.sh <MODEL> <OPERATION> <NUM_HOSTS> [<NUM_SECTIONS>] [host1,host2,...]"
  echo
  echo "MODEL:        The benchmark model to execute"
  echo "              Available models: cosmoflow, resnet50, unet3d, llama3-70b, llama3-405b, llama3-1t, llama3-8b"
  echo "OPERATION:    The operation to execute"
  echo "              Available operations: size, gen (not for checkpointing), run, report"
  echo "NUM_HOSTS:    The number of client hosts to be used for the benchmark"
  echo "NUM_SECTIONS: Number of sections to be written or read. ONLY AVAILABLE FOR CHECKPOINTING!"
  echo "HOSTS:        A comma-separated list of client host IPs or hostnames. Requires non-interactive SSH access."
}



case "$2" in
  "size" | "gen" | "run" | "report" )
    action="$2"
  ;;

  * )
    echo "Unknown action, use one of: gen, run"
    help
    exit 1
  ;;
esac

source "${HOME}/.venvs/myenv/bin/activate"
case "$1" in
  "resnet50" )
    resnet50 $action $3 $4
  ;;

  "cosmoflow" )
    cosmoflow $action $3 $4
  ;;

  "unet3d" )
    unet3d $action $3 $4
  ;;

  "llama*" )
    checkpoint $action $1 $3 $4 $5
  ;;

  * )
    help
    exit 1
  ;;
esac
