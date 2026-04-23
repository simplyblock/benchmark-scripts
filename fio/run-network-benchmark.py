#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def resolve_path(path: str) -> str:
    if path == "~":
        return os.path.expanduser("~")
    if path.startswith("~/"):
        return os.path.expanduser(path)
    home = os.path.expanduser("~")
    if path.startswith(f"{home}/~/"):
        return path.replace(f"{home}/~/", f"{home}/", 1)
    return path


def load_metadata(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if len(data.get("storage_nodes", [])) < 2:
        raise RuntimeError("cluster metadata must contain at least 2 storage_nodes")
    if len(data.get("clients", [])) < 1:
        raise RuntimeError("cluster metadata must contain at least 1 client")
    return data


def bits_to_gbps(bits_per_second: float) -> float:
    return bits_per_second / 1_000_000_000.0


def bits_to_gbytes_per_sec(bits_per_second: float) -> float:
    return bits_per_second / 8_000_000_000.0


def parse_iperf3_summary(raw_json: str, label: str) -> dict[str, Any]:
    data = json.loads(raw_json)
    end = data.get("end", {})
    sum_sent = end.get("sum_sent") or end.get("sum") or {}
    sum_received = end.get("sum_received") or end.get("sum") or {}

    tx_bps = float(sum_sent.get("bits_per_second", 0.0))
    rx_bps = float(sum_received.get("bits_per_second", tx_bps))
    retransmits = int(sum_sent.get("retransmits", 0))
    seconds = float(sum_sent.get("seconds", sum_received.get("seconds", 0.0)))

    return {
        "label": label,
        "seconds": seconds,
        "retransmits": retransmits,
        "tx_bps": tx_bps,
        "rx_bps": rx_bps,
        "tx_gbps": bits_to_gbps(tx_bps),
        "rx_gbps": bits_to_gbps(rx_bps),
        "tx_gbytes_per_sec": bits_to_gbytes_per_sec(tx_bps),
        "rx_gbytes_per_sec": bits_to_gbytes_per_sec(rx_bps),
    }


class SSHClients:
    def __init__(self, user: str, key_path: str, hosts: list[str]):
        try:
            import paramiko
        except Exception as exc:
            raise RuntimeError("paramiko is required (pip install paramiko)") from exc

        self.paramiko = paramiko
        self.user = user
        self.key_path = key_path
        self.hosts = list(hosts)
        self.clients: dict[str, "paramiko.SSHClient"] = {}
        self._connect_all(self.hosts)

    def _connect_host(self, host: str) -> None:
        client = self.paramiko.SSHClient()
        client.set_missing_host_key_policy(self.paramiko.AutoAddPolicy())
        client.connect(
            host,
            username=self.user,
            key_filename=self.key_path,
            timeout=10,
            banner_timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )
        self.clients[host] = client

    def _connect_all(self, hosts: list[str]) -> None:
        for host in hosts:
            self._connect_host(host)

    def _reconnect_host(self, host: str) -> None:
        old = self.clients.get(host)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        self._connect_host(host)

    def run(self, host: str, cmd: str, timeout: int = 900, check: bool = True) -> tuple[int, str, str]:
        if host not in self.clients:
            raise RuntimeError(f"no ssh client for host {host}")

        last_rc, last_out, last_err = -1, "", ""
        for attempt in range(1, 4):
            try:
                client = self.clients[host]
                _stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
                out = stdout.read().decode("utf-8", errors="replace")
                err = stderr.read().decode("utf-8", errors="replace")
                rc = stdout.channel.recv_exit_status()
                last_rc, last_out, last_err = rc, out, err
                if rc == -1 and attempt < 3:
                    log(f"SSH channel dropped on {host}; reconnecting (attempt {attempt}/3)")
                    self._reconnect_host(host)
                    continue
                if check and rc != 0:
                    raise RuntimeError(
                        f"remote command failed on {host} (rc={rc})\n"
                        f"command: {cmd}\n"
                        f"stdout:\n{out}\n"
                        f"stderr:\n{err}"
                    )
                return rc, out, err
            except Exception as exc:
                if attempt < 3:
                    log(f"SSH command error on {host}; reconnecting (attempt {attempt}/3): {exc}")
                    self._reconnect_host(host)
                    time.sleep(1)
                    continue
                if check:
                    raise RuntimeError(
                        f"remote command failed on {host} (rc={last_rc})\n"
                        f"command: {cmd}\n"
                        f"stdout:\n{last_out}\n"
                        f"stderr:\n{last_err}\n"
                        f"exception: {exc}"
                    ) from exc
        return last_rc, last_out, last_err

    def close(self) -> None:
        for client in self.clients.values():
            try:
                client.close()
            except Exception:
                pass


def ensure_iperf3(ssh: SSHClients, host: str) -> None:
    rc, _, _ = ssh.run(host, "command -v iperf3 >/dev/null 2>&1", check=False)
    if rc == 0:
        return
    log(f"iperf3 missing on {host}; installing via sudo -n dnf")
    ssh.run(host, "sudo -n dnf install -y iperf3")


def run_single_test(
    ssh: SSHClients,
    label: str,
    client_pub: str,
    server_pub: str,
    server_priv: str,
    port: int,
    duration: int,
    streams: int,
    out_file: Path,
) -> dict[str, Any]:
    log(f"Benchmark: {label} (client={client_pub} -> server={server_priv}:{port})")

    # Cleanup is best-effort: a transient SSH issue here should not abort the full benchmark run.
    cleanup_cmd = (
        "pids=$(ps -eo pid=,args= | "
        f"awk '/[i]perf3 -s -p {port}( |$)/ {{print $1}}'); "
        "[ -n \"$pids\" ] && kill $pids >/dev/null 2>&1 || true"
    )
    cleanup_rc, _, _ = ssh.run(server_pub, cleanup_cmd, check=False)
    if cleanup_rc != 0:
        log(f"Warning: pre-test iperf3 cleanup on {server_pub}:{port} returned rc={cleanup_rc}; continuing")
    ssh.run(server_pub, f"nohup iperf3 -s -p {port} --one-off >/tmp/iperf3-{port}.log 2>&1 &")
    ssh.run(
        server_pub,
        f"for _ in $(seq 1 10); do ss -lnt | grep -q ':{port} ' && exit 0; sleep 1; done; exit 1",
    )

    rc, out, err = ssh.run(
        client_pub,
        f"iperf3 -c {server_priv} -p {port} -P {streams} -t {duration} -J",
        timeout=duration + 120,
        check=False,
    )
    if rc != 0:
        _, server_log_out, server_log_err = ssh.run(server_pub, f"tail -n 80 /tmp/iperf3-{port}.log || true", check=False)
        raise RuntimeError(
            f"iperf3 failed for {label} (rc={rc})\n"
            f"client stdout:\n{out}\n"
            f"client stderr:\n{err}\n"
            f"server log:\n{server_log_out}\n{server_log_err}"
        )

    out_file.write_text(out, encoding="utf-8")
    log(f"Saved: {out_file}")
    summary = parse_iperf3_summary(out, label)
    log(
        f"Result {label}: "
        f"rx={summary['rx_gbps']:.2f} Gbps ({summary['rx_gbytes_per_sec']:.2f} GB/s), "
        f"tx={summary['tx_gbps']:.2f} Gbps ({summary['tx_gbytes_per_sec']:.2f} GB/s), "
        f"retransmits={summary['retransmits']}"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run iperf3 network benchmark across cluster nodes.")
    parser.add_argument("--cluster-metadata-file", default="cluster_metadata.json")
    parser.add_argument("--ssh-key-path", default="~/.ssh/gcp_sbcli")
    parser.add_argument("--duration-sec", type=int, default=30)
    parser.add_argument("--parallel-streams", type=int, default=8)
    parser.add_argument("--output-base-dir", default="results/network-benchmark")
    args = parser.parse_args()

    metadata_file = Path(args.cluster_metadata_file).resolve()
    if not metadata_file.exists():
        raise RuntimeError(f"cluster metadata not found: {metadata_file}")

    md = load_metadata(metadata_file)
    user = md.get("user") or "sbadmin"

    sn0 = md["storage_nodes"][0]
    sn1 = md["storage_nodes"][1]
    client = md["clients"][0]
    sn0_pub, sn0_priv = sn0["public_ip"], sn0["private_ip"]
    sn1_pub, sn1_priv = sn1["public_ip"], sn1["private_ip"]
    client_pub, client_priv = client["public_ip"], client["private_ip"]

    for name, value in [
        ("sn0 public_ip", sn0_pub),
        ("sn0 private_ip", sn0_priv),
        ("sn1 public_ip", sn1_pub),
        ("sn1 private_ip", sn1_priv),
        ("client public_ip", client_pub),
        ("client private_ip", client_priv),
    ]:
        if not value:
            raise RuntimeError(f"missing {name} in cluster metadata")

    key_path = resolve_path(args.ssh_key_path)
    if not Path(key_path).exists():
        raise RuntimeError(f"ssh key not found: {key_path}")

    out_dir = Path(args.output_base_dir).resolve() / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"Running iperf3 network benchmarks (duration={args.duration_sec}s, streams={args.parallel_streams})")

    hosts = sorted({sn0_pub, sn1_pub, client_pub})
    log(f"Opening persistent SSH clients for hosts: {', '.join(hosts)}")
    ssh = SSHClients(user=user, key_path=key_path, hosts=hosts)
    summaries: list[dict[str, Any]] = []
    try:
        log(f"Ensuring iperf3 on hosts: {', '.join(hosts)}")
        for host in hosts:
            ensure_iperf3(ssh, host)

        tests = [
            ("storage0-to-storage1", sn0_pub, sn1_pub, sn1_priv, 5201, out_dir / "storage0-to-storage1.json"),
            ("client-to-storage0", client_pub, sn0_pub, sn0_priv, 5202, out_dir / "client-to-storage0.json"),
            ("storage0-to-client", sn0_pub, client_pub, client_priv, 5203, out_dir / "storage0-to-client.json"),
        ]
        for label, cpub, spub, spriv, port, out_file in tests:
            summary = run_single_test(
                ssh=ssh,
                label=label,
                client_pub=cpub,
                server_pub=spub,
                server_priv=spriv,
                port=port,
                duration=args.duration_sec,
                streams=args.parallel_streams,
                out_file=out_file,
            )
            summaries.append(summary)
    finally:
        ssh.close()

    if summaries:
        log("Network throughput summary (receiver-side):")
        for summary in summaries:
            log(
                f"- {summary['label']}: "
                f"{summary['rx_gbps']:.2f} Gbps ({summary['rx_gbytes_per_sec']:.2f} GB/s), "
                f"retransmits={summary['retransmits']}"
            )
        rx_values = [s["rx_gbps"] for s in summaries]
        avg_rx = sum(rx_values) / len(rx_values)
        log(
            "Overall receiver throughput: "
            f"avg={avg_rx:.2f} Gbps ({bits_to_gbytes_per_sec(avg_rx * 1_000_000_000.0):.2f} GB/s), "
            f"min={min(rx_values):.2f} Gbps, max={max(rx_values):.2f} Gbps"
        )

    log(f"Network benchmark complete: {out_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
