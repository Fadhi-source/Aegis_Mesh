"""
Bootstrapper script to spin up the entire AegisMesh local ecosystem in one console.
Starts the Registry on port 8000, and 3 specific agents on 8101, 8102, 8103.
"""
import subprocess
import time
import sys
import psutil
import os

def kill_orphans(port_list):
    """Kills any existing processes bound to our target ports to prevent bind conflicts."""
    for c in psutil.net_connections(kind='tcp'):
        if c.laddr.port in port_list and c.status == 'LISTEN':
            try:
                p = psutil.Process(c.pid)
                p.terminate()
                print(f"[*] Terminated orphaned process {p.name()} on port {c.laddr.port}")
            except Exception:
                pass
    time.sleep(1)

def main():
    print("==================================================")
    print("AegisMesh Ecosystem Bootstrapper")
    print("==================================================")
    
    # 1. Ensure clean ports
    target_ports = [8000, 8101, 8102, 8103]
    kill_orphans(target_ports)

    venvexec = sys.executable  # Assumes we run this script using .venv/Scripts/python.exe
    
    procs = []

    print("[1/4] Starting AegisRegistry (Port 8000)...")
    print("[1/4] Starting AegisRegistry (Port 8000)...")
    cmd_reg = [venvexec, "-m", "uvicorn", "aegismesh.registry.main:app", "--host", "127.0.0.1", "--port", "8000"]
    p_reg = subprocess.Popen(cmd_reg, stdout=subprocess.DEVNULL, stderr=None)
    procs.append(p_reg)
    time.sleep(2) # Give the registry time to run its WAL recovery and start listening

    print("[2/4] Starting SystemMonitorAgent (Port 8101)...")
    cmd_sys = [venvexec, "-m", "uvicorn", "aegismesh.agents.sysmon_agent:app", "--host", "127.0.0.1", "--port", "8101"]
    p_sys = subprocess.Popen(cmd_sys, stdout=subprocess.DEVNULL, stderr=None)
    procs.append(p_sys)

    print("[3/4] Starting NetworkDiagnosticAgent (Port 8102)...")
    cmd_net = [venvexec, "-m", "uvicorn", "aegismesh.agents.netdiag_agent:app", "--host", "127.0.0.1", "--port", "8102"]
    p_net = subprocess.Popen(cmd_net, stdout=subprocess.DEVNULL, stderr=None)
    procs.append(p_net)

    print("[4/4] Starting WindowsEventLogAgent (Port 8103)...")
    cmd_win = [venvexec, "-m", "uvicorn", "aegismesh.agents.win_log_agent:app", "--host", "127.0.0.1", "--port", "8103"]
    p_win = subprocess.Popen(cmd_win, stdout=subprocess.DEVNULL, stderr=None)
    procs.append(p_win)

    # Wait for all agents to register before starting the Gateway
    time.sleep(4)

    print("[+] Starting AegisMesh Gateway (Port 9000) ...")
    cmd_gw = [venvexec, "-m", "uvicorn", "aegismesh.gateway.main:app", "--host", "127.0.0.1", "--port", "9000"]
    p_gw = subprocess.Popen(cmd_gw, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    procs.append(p_gw)

    print("\n[✔] AegisMesh ecosystem is fully active!")
    print("    - Registry running at: http://127.0.0.1:8000")
    print("    - Agents registering automatically...")
    print("\nPress Ctrl+C to terminate the entire mesh.")
    
    try:
        while True:
            time.sleep(1)
            # Check if any process crashed unexpectedly
            crashed = False
            for p in procs:
                if p.poll() is not None:
                    print(f"\n[!] ERROR: A component process (PID {p.pid}) died unexpectedly with exit code {p.returncode}.")
                    crashed = True
                    break
            if crashed:
                print("Shutting down entire mesh due to component failure.")
                break
    except KeyboardInterrupt:
        print("\n[*] Shutting down AegisMesh ecosystem...")
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait()
        print("[*] Shutdown complete.")

if __name__ == "__main__":
    main()
