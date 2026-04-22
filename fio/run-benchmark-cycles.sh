#!/usr/bin/env bash
set -euo pipefail

# Orchestrates benchmark cycles across:
# - Erasure coding profiles: 1+1, 2+2
# - Compression modes: off, on
# - Node states per run: all nodes up -> one node down -> node up (during migration)
#
# By default this runs benchmark definitions in sequence:
#   1) first-run.yaml
#   2) second-run.yaml
# During the node-up/migration phase it runs only:
#   3) third-run.yaml

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
FIO_RUNNER="${FIO_RUNNER:-${SCRIPT_DIR}/fio.py}"
WORKLOAD_DIR="${WORKLOAD_DIR:-${SCRIPT_DIR}}"
RESULTS_BASE_DIR="${RESULTS_BASE_DIR:-${SCRIPT_DIR}/results}"
SSH_KEY_PATH="${SSH_KEY_PATH:-~/.ssh/gcp_sbcli}"
CLUSTER_METADATA_FILE="${CLUSTER_METADATA_FILE:-${SCRIPT_DIR}/cluster_metadata.json}"
RUN_SEQUENCE=0
PRECREATION_DONE=0
CLUSTER_METADATA_LOADED=0
FIOBENCH_SYNC_DONE=0
SSH_CONTEXT_INITIALIZED=0
STAGE2_REMOTE=0
MGMT_IP=""
CLIENT_IP=""
SSH_USER=""
SSH_KEY_PATH_RESOLVED=""
REMOTE_BENCH_DIR=""
TMUX_SESSION_NAME=""
SSH_OPTS=()
PRECREATE_WORKERS_MAX=0
ACTIVE_VOLUME_ID=""

BENCHMARK_DEFINITIONS=(
  "${SCRIPT_DIR}/first-run.yaml"
  "${SCRIPT_DIR}/second-run.yaml"
)
POST_RECOVERY_BENCHMARK_DEFINITIONS=(
  "${SCRIPT_DIR}/third-run.yaml"
)

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

resolve_path() {
  local raw_path="$1"
  if [[ "${raw_path}" == "~" ]]; then
    printf '%s\n' "${HOME}"
    return
  fi

  if [[ "${raw_path}" == "~/"* ]]; then
    printf '%s/%s\n' "${HOME}" "${raw_path:2}"
    return
  fi

  if [[ "${raw_path}" == "${HOME}/~/"* ]]; then
    printf '%s/%s\n' "${HOME}" "${raw_path#${HOME}/~/}"
    return
  fi

  printf '%s\n' "${raw_path}"
}

load_cluster_metadata() {
  if [[ "${CLUSTER_METADATA_LOADED}" -eq 1 ]]; then
    return
  fi

  [[ -f "${CLUSTER_METADATA_FILE}" ]] || {
    echo "cluster metadata not found: ${CLUSTER_METADATA_FILE}" >&2
    exit 1
  }

  local -a _cluster_meta_values
  mapfile -t _cluster_meta_values < <("${PYTHON_BIN}" - "${CLUSTER_METADATA_FILE}" <<'PY'
import json
import sys

metadata_path = sys.argv[1]
with open(metadata_path, "r", encoding="utf-8") as f:
    m = json.load(f)

mgmt_ip = m.get("mgmt", {}).get("public_ip")
clients = m.get("clients", [])
client_ip = clients[0].get("public_ip") if clients else None
ssh_user = m.get("user") or "sbadmin"

if not mgmt_ip:
    raise SystemExit("missing mgmt.public_ip in cluster metadata")
if not client_ip:
    raise SystemExit("missing clients[0].public_ip in cluster metadata")

print(mgmt_ip)
print(client_ip)
print(ssh_user)
PY
  )

  MGMT_IP="${_cluster_meta_values[0]}"
  CLIENT_IP="${_cluster_meta_values[1]}"
  SSH_USER="${_cluster_meta_values[2]}"
  CLUSTER_METADATA_LOADED=1
}

init_ssh_context() {
  if [[ "${SSH_CONTEXT_INITIALIZED}" -eq 1 ]]; then
    return
  fi

  SSH_KEY_PATH_RESOLVED="$(resolve_path "${SSH_KEY_PATH}")"
  [[ -f "${SSH_KEY_PATH_RESOLVED}" ]] || {
    echo "ssh key not found: ${SSH_KEY_PATH_RESOLVED}" >&2
    exit 1
  }

  SSH_OPTS=(
    -o BatchMode=yes
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o LogLevel=ERROR
    -i "${SSH_KEY_PATH_RESOLVED}"
  )
  SSH_CONTEXT_INITIALIZED=1
}

ssh_to_host() {
  local host="$1"
  shift
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${host}" "$@"
}

run_on_mgmt() {
  local cmd="$1"
  ssh_to_host "${MGMT_IP}" "${cmd}"
}

run_on_client() {
  local cmd="$1"
  if [[ "${STAGE2_REMOTE}" -eq 1 ]]; then
    bash -lc "${cmd}"
  else
    ssh_to_host "${CLIENT_IP}" "${cmd}"
  fi
}

compute_precreate_workers_max() {
  if [[ "${PRECREATE_WORKERS_MAX}" -gt 0 ]]; then
    return
  fi

  local max_workers
  max_workers="$("${PYTHON_BIN}" - "${BENCHMARK_DEFINITIONS[@]}" "${POST_RECOVERY_BENCHMARK_DEFINITIONS[@]}" <<'PY'
import sys
import yaml
from pathlib import Path

max_workers = 0
for benchmark_file in sys.argv[1:]:
    benchmark = yaml.safe_load(Path(benchmark_file).read_text())
    workers = benchmark.get("benchmark", {}).get("workers", [])
    for w in workers:
        try:
            max_workers = max(max_workers, int(w))
        except (TypeError, ValueError):
            pass

if max_workers <= 0:
    raise SystemExit("failed to determine max workers from benchmark yaml files")

print(max_workers)
PY
  )"

  PRECREATE_WORKERS_MAX="${max_workers}"
}

resolve_remote_path() {
  local raw_path="$1"
  if [[ "${raw_path}" == "~" ]]; then
    printf '/home/%s\n' "${SSH_USER}"
    return
  fi
  if [[ "${raw_path}" == "~/"* ]]; then
    printf '/home/%s/%s\n' "${SSH_USER}" "${raw_path:2}"
    return
  fi
  printf '%s\n' "${raw_path}"
}

get_primary_storage_node_id_for_volume() {
  local volume_id="$1"
  [[ -n "${volume_id}" ]] || {
    echo "volume id is required to determine primary storage node id" >&2
    exit 1
  }

  local volume_details_json
  volume_details_json="$(run_on_mgmt "sbctl volume get ${volume_id} --json")"

  printf '%s\n' "${volume_details_json}" | "${PYTHON_BIN}" -c '
import json
import sys

payload = json.load(sys.stdin)
if isinstance(payload, list):
    if not payload:
        raise SystemExit("sbctl volume get returned an empty list")
    payload = payload[0]

if not isinstance(payload, dict):
    raise SystemExit("unexpected sbctl volume get payload type")

nodes = payload.get("nodes")
if isinstance(nodes, list) and nodes and isinstance(nodes[0], str) and nodes[0]:
    print(nodes[0])
    raise SystemExit(0)

node_id = payload.get("node_id")
if isinstance(node_id, str) and node_id:
    print(node_id)
    raise SystemExit(0)

raise SystemExit("missing usable node id; expected nodes[0] in sbctl volume get output")
'
}

# -----------------------------------------------------------------------------
# Placeholder functions - fill these with simplyblock-specific implementation.
# -----------------------------------------------------------------------------

create_volume() {
  require_cmd ssh
  load_cluster_metadata
  init_ssh_context

  local volume_name
  volume_name="${VOLUME_NAME:-benchmark-$(date '+%Y%m%d-%H%M%S')}"

  log "Creating volume '${volume_name}' (mgmt=${MGMT_IP}, client=${CLIENT_IP})"

  local pool_json
  pool_json="$(run_on_mgmt "sbctl pool list --json")"

  local pool_id
  pool_id="$(printf '%s\n' "${pool_json}" | "${PYTHON_BIN}" -c '
import json
import sys

pools = json.load(sys.stdin)
if not pools:
    raise SystemExit("no storage pools returned by sbctl")
pool_id = pools[0].get("UUID")
if not pool_id:
    raise SystemExit("missing UUID in first pool entry")
print(pool_id)
')"

  run_on_mgmt "sbctl volume add ${volume_name} 1500G ${pool_id}"

  local volume_json
  volume_json="$(run_on_mgmt "sbctl volume list --json")"

  local volume_id
  volume_id="$(printf '%s\n' "${volume_json}" | "${PYTHON_BIN}" -c '
import json
import sys

volumes = json.load(sys.stdin)
target_name = sys.argv[1]
target_id = None
for v in volumes:
    if v.get("Name") == target_name and v.get("Id"):
        target_id = v["Id"]
if not target_id:
    raise SystemExit(f"volume \"{target_name}\" not found in sbctl volume list")
print(target_id)
' "${volume_name}")"
  ACTIVE_VOLUME_ID="${volume_id}"

  local connect_commands_raw
  connect_commands_raw="$(run_on_mgmt "sbctl volume connect ${volume_id}")"

  local connect_commands
  connect_commands="$(printf '%s\n' "${connect_commands_raw}" | sed 's/--nr-io-queues=3/--nr-io-queues=6/g')"

  log "Connecting volume ${volume_id} on client ${CLIENT_IP}"
  while IFS= read -r cmd; do
    [[ -n "${cmd// }" ]] || continue
    run_on_client "${cmd}"
  done <<< "${connect_commands}"

  log "Volume '${volume_name}' created and connected (pool=${pool_id}, volume_id=${volume_id})"
}

format_and_mount_volume() {
  require_cmd ssh
  load_cluster_metadata
  init_ssh_context

  log "Formatting and mounting /dev/nvme0n1 on client ${CLIENT_IP}"
  run_on_client "sudo mkfs.xfs -f /dev/nvme0n1"
  run_on_client "sudo mkdir -p /media"
  run_on_client "sudo mount /dev/nvme0n1 /media"
  run_on_client "sudo chown ${SSH_USER}:${SSH_USER} /media"

  local mount_line
  mount_line="$(run_on_client "mount | grep ' on /media ' || true")"
  [[ -n "${mount_line}" ]] || {
    echo "mount verification failed: /media is not mounted on client ${CLIENT_IP}" >&2
    exit 1
  }
  [[ "${mount_line}" == /dev/nvme0n1* ]] || {
    echo "mount verification failed: /media is mounted, but not from /dev/nvme0n1: ${mount_line}" >&2
    exit 1
  }
  run_on_client "touch /media/.sb-write-test && rm -f /media/.sb-write-test"

  log "Mount verified: ${mount_line}"
}

sync_fiobench_to_client() {
  require_cmd ssh
  require_cmd tar
  load_cluster_metadata
  init_ssh_context

  if [[ "${FIOBENCH_SYNC_DONE}" -eq 1 ]]; then
    return
  fi

  local remote_bench_dir="${CLIENT_BENCH_DIR:-/home/${SSH_USER}/fiobench}"
  REMOTE_BENCH_DIR="${remote_bench_dir}"

  log "Syncing fiobench assets to client ${CLIENT_IP}:${remote_bench_dir}"
  ssh_to_host "${CLIENT_IP}" "mkdir -p '${remote_bench_dir}'"

  COPYFILE_DISABLE=1 COPY_EXTENDED_ATTRIBUTES_DISABLE=1 tar -C "${SCRIPT_DIR}" -cf - \
    fiobench \
    workloads \
    fio.py \
    run-benchmark-cycles.sh \
    first-run.yaml \
    second-run.yaml \
    third-run.yaml \
    benchmark.yaml \
    | ssh_to_host "${CLIENT_IP}" "tar -xf - -C '${remote_bench_dir}'"

  cat "${CLUSTER_METADATA_FILE}" | ssh_to_host "${CLIENT_IP}" "cat > '${remote_bench_dir}/cluster_metadata.json'"

  ssh_to_host "${CLIENT_IP}" \
    "test -f '${remote_bench_dir}/fio.py' && test -f '${remote_bench_dir}/run-benchmark-cycles.sh' && test -f '${remote_bench_dir}/cluster_metadata.json' && test -d '${remote_bench_dir}/fiobench' && test -d '${remote_bench_dir}/workloads'"

  local remote_ssh_key_path
  remote_ssh_key_path="$(resolve_remote_path "${SSH_KEY_PATH}")"
  local remote_ssh_key_dir
  remote_ssh_key_dir="$(dirname "${remote_ssh_key_path}")"
  ssh_to_host "${CLIENT_IP}" "mkdir -p '${remote_ssh_key_dir}' && chmod 700 '${remote_ssh_key_dir}'"
  cat "${SSH_KEY_PATH_RESOLVED}" | ssh_to_host "${CLIENT_IP}" "cat > '${remote_ssh_key_path}' && chmod 600 '${remote_ssh_key_path}'"

  FIOBENCH_SYNC_DONE=1
  log "fiobench assets synced to client"
}

ensure_client_python_deps() {
  require_cmd ssh
  load_cluster_metadata
  init_ssh_context

  log "Ensuring Python dependencies on client ${CLIENT_IP}"
  ssh_to_host "${CLIENT_IP}" "python3 -m pip --version >/dev/null 2>&1 || sudo python3 -m ensurepip --upgrade >/dev/null 2>&1 || true"
  ssh_to_host "${CLIENT_IP}" "python3 -m pip install --user --upgrade pip >/dev/null 2>&1 || true"
  ssh_to_host "${CLIENT_IP}" "python3 -m pip install --user PyYAML"
}

launch_stage2_in_tmux() {
  require_cmd ssh
  load_cluster_metadata
  init_ssh_context
  sync_fiobench_to_client
  ensure_client_python_deps

  local remote_bench_dir="${REMOTE_BENCH_DIR:-${CLIENT_BENCH_DIR:-/home/${SSH_USER}/fiobench}}"
  local session_name="${TMUX_SESSION_NAME:-sb-bench-$(date '+%Y%m%d-%H%M%S')}"
  local log_file="${remote_bench_dir}/run-benchmark-cycles.log"
  local stage2_cmd
  printf -v stage2_cmd 'cd %q && chmod +x run-benchmark-cycles.sh && ./run-benchmark-cycles.sh --stage2-remote 2>&1 | tee -a %q' \
    "${remote_bench_dir}" "${log_file}"

  ssh_to_host "${CLIENT_IP}" "command -v tmux >/dev/null 2>&1 || { echo 'tmux not installed on client'; exit 1; }"
  ssh_to_host "${CLIENT_IP}" "tmux has-session -t ${session_name@Q} 2>/dev/null && tmux kill-session -t ${session_name@Q} || true"
  ssh_to_host "${CLIENT_IP}" "tmux new-session -d -s ${session_name@Q}"
  ssh_to_host "${CLIENT_IP}" "tmux send-keys -t ${session_name@Q} ${stage2_cmd@Q} C-m"
  ssh_to_host "${CLIENT_IP}" "tmux has-session -t ${session_name@Q}"

  log "Stage 2 started on client in tmux session '${session_name}'"
  log "Attaching to tmux session '${session_name}' on client ${CLIENT_IP}"
  ssh -tt "${SSH_OPTS[@]}" "${SSH_USER}@${CLIENT_IP}" "tmux attach -t ${session_name@Q}"
}

disable_compression() {
  require_cmd ssh
  load_cluster_metadata
  init_ssh_context

  local metadata_file="${CLUSTER_METADATA_FILE}"
  [[ -f "${metadata_file}" ]] || {
    echo "cluster metadata not found: ${metadata_file}" >&2
    exit 1
  }

  local -a storage_node_ips
  mapfile -t storage_node_ips < <("${PYTHON_BIN}" - "${metadata_file}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    m = json.load(f)

nodes = m.get("storage_nodes", [])
if not isinstance(nodes, list) or not nodes:
    raise SystemExit("no storage_nodes found in cluster metadata")

for n in nodes:
    if not isinstance(n, dict):
        continue
    ip = n.get("private_ip") or n.get("public_ip")
    if ip:
        print(ip)
PY
  )

  [[ "${#storage_node_ips[@]}" -gt 0 ]] || {
    echo "no storage node IPs resolved from cluster metadata" >&2
    exit 1
  }

  local storage_ip
  for storage_ip in "${storage_node_ips[@]}"; do
    log "Disabling compression on storage node ${storage_ip}"
    ssh_to_host "${storage_ip}" "bash -lc 'set -euo pipefail
container_name=\$(docker ps --format \"{{.Names}}\" | grep -E \"^spdk_80[0-9]{2}$\" | head -n1 || true)
if [[ -z \"\${container_name}\" ]]; then
  echo \"no spdk_80xx container found on ${storage_ip}\" >&2
  exit 1
fi
jm_vuid=\$(docker logs \"\${container_name}\" 2>&1 | grep -oE \"jm_vuid=[0-9]+\" | tail -n1 | cut -d= -f2 || true)
if [[ -z \"\${jm_vuid}\" ]]; then
  echo \"failed to resolve jm_vuid from docker logs for \${container_name} on ${storage_ip}\" >&2
  exit 1
fi
cat > /tmp/disable_compression.json <<EOF
{
  \"subsystems\": [
    {
      \"subsystem\": \"bdev\",
      \"config\": [
        {
          \"method\": \"jc_suspend_compression\",
          \"params\": {
            \"jm_vuid\": \${jm_vuid},
            \"suspend\": true
          }
        }
      ]
    }
  ]
}
EOF
docker cp /tmp/disable_compression.json \"\${container_name}:/root/disable_compression.json\"
docker exec \"\${container_name}\" bash -lc \"/root/spdk/scripts/rpc_sock.py /root/disable_compression.json /mnt/ramdisk/\${container_name}/spdk.lock\"'"
  done
}

bring_down_one_storage_node() {
  require_cmd ssh
  load_cluster_metadata
  init_ssh_context

  local volume_id="${PRIMARY_VOLUME_ID:-${ACTIVE_VOLUME_ID}}"
  [[ -n "${volume_id}" ]] || {
    echo "no volume id available for node shutdown; set PRIMARY_VOLUME_ID or run create_volume first" >&2
    exit 1
  }
  log "Finding primary storage node for volume ${volume_id} on management node ${MGMT_IP}"

  local storage_node_id
  storage_node_id="$(get_primary_storage_node_id_for_volume "${volume_id}")"

  log "Shutting down storage node ${storage_node_id} (force)"
  run_on_mgmt "sbctl sn shutdown ${storage_node_id} --force"
}

bring_up_storage_node() {
  require_cmd ssh
  load_cluster_metadata
  init_ssh_context

  local volume_id="${PRIMARY_VOLUME_ID:-${ACTIVE_VOLUME_ID}}"
  [[ -n "${volume_id}" ]] || {
    echo "no volume id available for node restart; set PRIMARY_VOLUME_ID or run create_volume first" >&2
    exit 1
  }
  log "Finding primary storage node for volume ${volume_id} on management node ${MGMT_IP}"

  local storage_node_id
  storage_node_id="$(get_primary_storage_node_id_for_volume "${volume_id}")"

  log "Restarting storage node ${storage_node_id} (force)"
  run_on_mgmt "sbctl sn restart ${storage_node_id} --force"

  local wait_timeout_sec="${SN_RESTART_WAIT_TIMEOUT_SEC:-300}"
  local wait_interval_sec="${SN_RESTART_WAIT_INTERVAL_SEC:-5}"
  local deadline=$((SECONDS + wait_timeout_sec))
  local node_is_online=0

  log "Waiting for storage node ${storage_node_id} to report online"
  while (( SECONDS < deadline )); do
    local sn_list_json
    sn_list_json="$(run_on_mgmt "sbctl sn list --json")"

    if printf '%s\n' "${sn_list_json}" | "${PYTHON_BIN}" - "${storage_node_id}" <<'PY'
import json
import sys

target_id = sys.argv[1]
nodes = json.load(sys.stdin)
if not isinstance(nodes, list):
    raise SystemExit(1)

for node in nodes:
    if not isinstance(node, dict):
        continue
    uuid = node.get("UUID") or node.get("uuid")
    status = str(node.get("Status") or node.get("status") or "").strip().lower()
    if uuid == target_id and status == "online":
        raise SystemExit(0)

raise SystemExit(1)
PY
    then
      node_is_online=1
      break
    fi
    sleep "${wait_interval_sec}"
  done

  if [[ "${node_is_online}" -ne 1 ]]; then
    echo "storage node ${storage_node_id} did not become online within ${wait_timeout_sec}s" >&2
    exit 1
  fi
  log "Storage node ${storage_node_id} is online"
}

# -----------------------------------------------------------------------------
# Benchmark orchestration.
# -----------------------------------------------------------------------------

run_single_benchmark_definition() {
  local benchmark_file="$1"
  local _unused_worker_dir="${2:-}"
  compute_precreate_workers_max
  local -a fio_run_args=(
    "${PYTHON_BIN}" "./fio.py" run
    --benchmark-file "__BENCHMARK_FILE__"
    --worker-dir "/media"
    --workload-dir "__WORKLOAD_DIR__"
  )

  load_cluster_metadata
  init_ssh_context

  local remote_bench_dir
  remote_bench_dir="${CLIENT_BENCH_DIR:-/home/${SSH_USER}/fiobench}"
  local benchmark_name
  benchmark_name="$(basename "${benchmark_file}")"
  local remote_benchmark_file="${remote_bench_dir}/${benchmark_name}"

  if [[ "${PRECREATION_DONE}" -eq 0 ]]; then
    log "Running benchmark definition with precreation: ${benchmark_file}"
    fio_run_args=("env" "FIO_PRECREATE_NUMJOBS=${PRECREATE_WORKERS_MAX}" "${fio_run_args[@]}")
    PRECREATION_DONE=1
  else
    log "Running benchmark definition with --skip-precreation: ${benchmark_file}"
    fio_run_args+=(--skip-precreation)
  fi

  fio_run_args=("${fio_run_args[@]/__BENCHMARK_FILE__/${remote_benchmark_file}}")
  fio_run_args=("${fio_run_args[@]/__WORKLOAD_DIR__/${remote_bench_dir}}")

  local remote_cmd
  printf -v remote_cmd 'cd %q &&' "${remote_bench_dir}"
  local arg
  for arg in "${fio_run_args[@]}"; do
    printf -v remote_cmd '%s %q' "${remote_cmd}" "${arg}"
  done

  log "Executing fio benchmark on client ${CLIENT_IP} from ${remote_bench_dir} (worker-dir=/media)"
  if [[ "${STAGE2_REMOTE}" -eq 1 ]]; then
    bash -lc "${remote_cmd}"
  else
    require_cmd ssh
    ssh_to_host "${CLIENT_IP}" "${remote_cmd}"
  fi
}

run_benchmark_set() {
  local run_dir="$1"
  shift
  local benchmark_definitions=("$@")

  local i=1
  local benchmark_file
  for benchmark_file in "${benchmark_definitions[@]}"; do
    RUN_SEQUENCE=$((RUN_SEQUENCE + 1))
    local benchmark_name
    benchmark_name="$(basename "${benchmark_file}" .yaml)"
    local unique_worker_dir
    unique_worker_dir="${run_dir}/bench-${i}-${benchmark_name}-$(date '+%Y%m%d-%H%M%S')-${RUN_SEQUENCE}"
    run_single_benchmark_definition "${benchmark_file}" "${unique_worker_dir}"
    i=$((i + 1))
  done
}

run_node_state_cycle() {
  local run_dir="$1"

  log "Phase 1/3: all storage nodes up"
  run_benchmark_set "${run_dir}/all-nodes-up" "${BENCHMARK_DEFINITIONS[@]}"

  log "Phase 2/3: bring one node down"
  bring_down_one_storage_node
  run_benchmark_set "${run_dir}/one-node-down" "${BENCHMARK_DEFINITIONS[@]}"

  log "Phase 3/3: bring node up (during migration)"
  bring_up_storage_node
  run_benchmark_set "${run_dir}/node-up-during-migration" "${POST_RECOVERY_BENCHMARK_DEFINITIONS[@]}"
}

prepare_run_config() {
  local erasure_profile="$1"
  local compression_mode="$2"

  # Create + mount volume before each erasure profile run, as requested.
  # Erasure coding profile must be set manually before starting each run.
  create_volume
  format_and_mount_volume

  case "${compression_mode}" in
    "off")
      disable_compression
      ;;
    "on")
      # Compression is enabled by default; no action required.
      ;;
    *)
      echo "unsupported compression mode: ${compression_mode}" >&2
      exit 1
      ;;
  esac
}

run_full_matrix() {
  local timestamp
  timestamp="$(date '+%Y%m%d-%H%M%S')"

  local matrix=(
    "1+1 off"
    "1+1 on"
    "2+2 off"
    "2+2 on"
  )

  local item
  for item in "${matrix[@]}"; do
    # shellcheck disable=SC2086
    set -- ${item}
    local ec_profile="$1"
    local compression="$2"

    local run_name="ec-${ec_profile//+/p}-compression-${compression}"
    local run_dir="${RESULTS_BASE_DIR}/${timestamp}/${run_name}"

    log "================================================================="
    log "Starting run: erasure=${ec_profile}, compression=${compression}"
    log "Results dir: ${run_dir}"

    prepare_run_config "${ec_profile}" "${compression}"
    run_node_state_cycle "${run_dir}"

    log "Completed run: erasure=${ec_profile}, compression=${compression}"
  done
}

main() {
  if [[ "${1:-}" == "--stage2-remote" ]]; then
    STAGE2_REMOTE=1
    shift
  fi
  if [[ "$#" -gt 0 ]]; then
    echo "unknown arguments: $*" >&2
    exit 1
  fi

  require_cmd "${PYTHON_BIN}"

  # Validate benchmark definition files early.
  local benchmark_file
  for benchmark_file in "${BENCHMARK_DEFINITIONS[@]}"; do
    [[ -f "${benchmark_file}" ]] || {
      echo "benchmark definition not found: ${benchmark_file}" >&2
      exit 1
    }
  done
  for benchmark_file in "${POST_RECOVERY_BENCHMARK_DEFINITIONS[@]}"; do
    [[ -f "${benchmark_file}" ]] || {
      echo "benchmark definition not found: ${benchmark_file}" >&2
      exit 1
    }
  done

  if [[ "${STAGE2_REMOTE}" -eq 0 ]]; then
    launch_stage2_in_tmux
    return
  fi

  mkdir -p "${RESULTS_BASE_DIR}"
  run_full_matrix
  log "All benchmark cycles completed."
}

main "$@"
