#!/usr/bin/env bash

set -e

PSQL="$(command -v psql)"
if ! [[ "${PSQL}" ]]; then
  echo "psql not installed"
  exit 1
fi

function install_postgresql_17_redhat() {
  sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm
  sudo dnf -qy module disable postgresql
  sudo dnf install -y postgresql17-server postgresql-contrib
}

function prepare_postgresql_17() {
  sudo mkdir -p /var/lib/pgsql/17/data
  sudo mount /dev/benchmark/bm /var/lib/pgsql/17/data/
  sudo chown -R postgres:postgres /var/lib/pgsql/17/data
  sudo /usr/pgsql-17/bin/postgresql-17-setup initdb
  echo "max_connections=500" >> /var/lib/pgsql/17/data/postgresql.conf
  echo "shared_buffers = 1GB" >> /var/lib/pgsql/17/data/postgresql.conf
  echo "huge_pages = on" >> /var/lib/pgsql/17/data/postgresql.conf
  echo "max_parallel_workers = 16" >> /var/lib/pgsql/17/data/postgresql.conf
  echo "jit = off" >> /var/lib/pgsql/17/data/postgresql.conf
  systemctl enable postgresql-17
  systemctl start postgresql-17
  su postgres -c "psql -c 'create database benchmark;'"
}

function pgbench_create_testdata() {
  su postgres -c "pgbench -i --fillfactor=90 -s 10000 benchmark"
}

function run_pgbench() {
  workers=$1
  threads=$2
  runtime=$3
  workload="$4"
  log_dir="$6"

  echo -n "Dropping caches... "
  sync
  echo 3 > /proc/sys/vm/drop_caches
  echo "done."

  echo "Running benchmark iteration ${5} with workload=${workload}, runtime=${runtime}, workers=${workers}, threads=${threads}..."
  mkdir -p "${log_dir}"
  chown -R postgres:postgres "${log_dir}"
  logprefix="${log_dir}/pgbench--${5}--${workload}--${workers}--${threads}"
  su postgres -c "pgbench --protocol=prepared -c ${workers} -j ${threads} -T ${runtime} -P 2 --builtin=${workload} -l --log-prefix=${logprefix} benchmark"
}

function run_pgbench_tpcb() {
  run_pgbench $1 $2 $3 tpcb-like $4 "$5"
}

function run_pgbench_readonly() {
  run_pgbench $1 $2 $3 select $4 "$5"
}

function run_pgbench_update() {
  run_pgbench $1 $2 $3 simple-update $4 "$5"
}

case "$1" in
  "tpcb" )
    run_pgbench_tpcb 50 20 1800 1 "$2"
    run_pgbench_tpcb 100 20 1800 1 "$2"
    run_pgbench_tpcb 200 20 1800 1 "$2"
  ;;

  "readonly" )
    run_pgbench_readonly 50 20 1800 1 "$2"
    run_pgbench_readonly 100 20 1800 1 "$2"
    run_pgbench_readonly 200 20 1800 1 "$2"
  ;;

  "update" )
    run_pgbench_update 50 20 1800 1 "$2"
    run_pgbench_update 100 20 1800 1 "$2"
    run_pgbench_update 200 20 1800 1 "$2"
  ;;

  * )
    echo "pgbench.sh [tpcb|readonly|update] [./logs]"
  ;;
esac
