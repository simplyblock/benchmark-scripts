#!/usr/bin/env bash

# Stop on any error!
set -e

scriptbase="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"

function build_workload_jobfile() {
  local workload_file="$1"
  local worker_dir="$2"
  cat "${workload_file}" "${scriptbase}/workloads/global" > "${worker_dir}/job.fio"
}

function run_fio() {
  local worker_dir="$1"
  local loops=$2
  local workload="$3"

  for ((n=0;n<$loops;n++)); do
    echo "Running workload ${workload} loop #$((n+1))..."
    fio --output="${worker_dir}"/"${workload}"-${n}.json --output-format=json "${worker_dir}/job.fio"
  done
}

function get_workload_name() {
  local workload_file="$1"
  awk -F '=' '/name/ {print $2}' "${workload_file}"
}

function get_workload_description() {
  local workload_file="$1"
  awk -F '=' '/description/ {print $2}' "${workload_file}"
}

function run_single_workload() {
  local workload_file="$1"
  local worker_dir="$2"
  local loops=$3

  local workload_name="$(get_workload_name "${workload_file}")"

  echo "Running benchmark for workload $workload_name..."
  build_workload_jobfile "${workload_file}" "${worker_dir}"
  run_fio "${worker_dir}" "${loops}"
}

function run_workloads() {
  local worker_dir="$1"
  local loops=$2
  local workload_files=("${@:3}")

  for workload_file in "${workload_files[@]}"; do
    run_single_workload "${workload_file}" "${worker_dir}" "${loops}"
  done
}

function list_available_workloads() {
  for workload_file in "${scriptbase}"/workloads/*.fio; do
    local workload_name="$(get_workload_name "${workload_file}")"
    local workload_description="$(get_workload_description "${workload_file}")"

    echo "- Workload: ${workload_name}:"
    echo "            ${workload_description}"
  done
}

case $1 in
  list-workloads )
    list_available_workloads
  ;;

  run-all )
    worker_dir="$(cd "$(dirname "$2")"; pwd)/$(basename "$2")"
    loops=$3

    i=1
    workloads=()
    for workload_file in "${scriptbase}"/workloads/*.fio; do
      workloads[i]="${workload_file}"
      i=$((i+1))
    done

    run_workloads "${worker_dir}" "${loops}" "${workloads[@]}"
  ;;
esac
