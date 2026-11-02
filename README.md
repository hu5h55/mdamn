# `mdamn`

![Version](https://img.shields.io/badge/version-17.0.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

**`mdamn`** is a single-file, zero-dependency Linux `mdadm` & SMART interactive TUI and static CLI monitor.

---

## Features

- **Interactive Arrow-Key TUI & Static CLI (`--static`)**:
  - Navigate between `mdadm` arrays and physical drives (`↑`/`↓` or `k`/`j`).
  - Auto-falls back to static output when piped or executed from cron/MOTD scripts.
- **Live Kernel Telemetry (`/sys/block/mdX/md/` & `/proc/mdstat`)**:
  - Real-time array state, RAID level, active/failed/spare counters, mismatch counts, sector progress, and scrub speed.
  - Live 2-second `/sys/block/mdX/md/` refresh loop without waking spun-down drives or re-running heavy SMART scans.
- **Hardware Approval Checkpoint (`[y] Yes` / `[n] No` / `[i] Info & Impact`)**:
  - Pause & save parity check sector offset (`sync_min`), resume from saved sector, run full parity check, stop & reset, or trigger parity repair.
  - Switch kernel scrub speed limits on the fly (`50 MB/s` Quiet, `200 MB/s` System Default, `1,000 MB/s` Turbo).
  - Every hardware command opens a decluttered approval window with default focus on **`[i] Info & Impact`**, showing the exact `/sys/block/mdX/md/` write and physical disk impact before execution.
- **Drive SMART Inspector**:
  - Per-drive health (`OK`/`CRIT`), temperature, power-on time (`Yy Mm`), capacity, reallocated/pending/uncorrectable sector counts, and expandable full SMART attribute table.

---

## Requirements

- **OS**: Linux with `mdadm` kernel support
- **Python**: Python 3.7+ (standard library only)
- **System Utilities**: `mdadm`, `smartmontools` (`smartctl`), `parted`, `util-linux` (`lsblk`)

```bash
sudo apt update && sudo apt install -y mdadm parted smartmontools util-linux
```

---

## Installation & Usage

```bash
git clone https://github.com/hu5h55/mdamn.git
cd mdamn
chmod +x mdamn.py
sudo ./mdamn.py
```

On first run as root, `mdamn.py` links `/usr/local/bin/mdamn` so you can run `mdamn` from anywhere:

```bash
mdamn                 # Launch interactive TUI
mdamn --static        # Print static summary and exit (cron / MOTD)
mdamn --pause [md]    # Pause active parity check and save sector offset
mdamn --resume [md]   # Resume parity check from saved sector offset
mdamn --check-full    # Reset sync_min=0 and start full parity check
mdamn --stop-check    # Stop check and reset sync_min=0
mdamn --version       # Print version
mdamn --help          # Print help
```
