# RAID Health Monitor (`raid`) (v16.0.1)

An interactive terminal UI (TUI) and command-line health monitor & control suite for Linux **MDADM software RAID arrays** and physical drives.

Combines array-level status and sysfs control from `/proc/mdstat`, `/sys/block/md*/md/`, and `mdadm` with drive-level SMART metrics (`smartctl`) and disk attributes (`parted`, `lsblk`, `/sys/block`) into both an interactive arrow-key interface and a static report mode for cron/MOTD scripts.

---

## Features

- **Interactive Arrow-Key TUI (Default in Terminal)**:
  - Navigate up and down (`↑` / `↓` or `k` / `j`) across RAID arrays and individual disks.
  - Press `Enter` on a **RAID array** to open the **RAID Control & Telemetry Modal**:
    - **Pause Check / Scrub** (preserves exact sector progress in `sync_min` while dropping array scrub I/O to zero).
    - **Resume Check / Scrub** (continues from saved `sync_min` sector offset).
    - **Start Full Check from 0%** (resets `sync_min=0` and starts a complete parity scrub, with confirmation safeguard).
    - **Stop & Reset Check** (sets `idle` and clears `sync_min`).
    - **Start Parity Repair Scrub** (`repair`).
    - **Adjust Scrub Speed Profile** (*Quiet 50 MB/s*, *System Default 200 MB/s*, *Turbo 1,000 MB/s*).
    - **Toggle Full `mdadm --detail` Output**.
  - Press `Enter` on a **Drive** (or `a` for all drives) to expand an inline **Hardware & SMART Inspector**:
    - Full untruncated Model, Serial Number, Firmware Version, Rotation Rate, Form Factor, and SATA/Interface speed.
    - Exact Power-On Hours (with years/days breakdown), Power Cycles, Load Cycles, and Start/Stop counts.
    - MD array slot, kernel sync state, and read error counters (or partition/mount layout for standalone drives).
    - Critical SMART attribute table (`Raw_Read_Error_Rate`, `Reallocated_Sector_Ct`, `Current_Pending_Sector`, `Offline_Uncorrectable`, `Temperature_Celsius`, etc.) and latest SMART self-test log.
  - Live 2-second auto-refresh of RAID scrub progress, speed, and ETA.
- **Static Mode (`--static` / `-s`) & Non-TTY Auto-Detection**:
  - Prints a clean, color-coded static summary for MOTD welcome screens (`/etc/update-motd.d/95-raid`) and cron jobs.
  - Automatically activates when output is redirected (`raid > /home/hu5h/raid.out`).
- **Direct CLI Control Flags**:
  - `--pause [md]`, `--resume [md]`, `--check-full [md]`, `--stop-check [md]`.

---

## Sample Static Output (`raid --static`)

```text
■ CHECKING  /dev/md6  raid5    6/6 devices    Usage: ▰▰▰▰▰▱▱▱▱▱ 46% (42T/91T)     (/mnt/storage)    Scrub: ▰▰▱▱▱▱▱▱ 23.0% (128.9 MB/s · 1d 7h (ETA: Nov 03, 12:41))
  Model                    Device     Serial     Size     Type   POH    Temp     Errors             State
  ATA-WDC-WD201KFGX-68     /dev/sdc   ..HMGLER   20.0TB   HDD    20k    41°C     -                  active sync
  ATA-WDC-WD202KFGX-68     /dev/sdd   ..GH57LH   20.0TB   HDD    1k     40°C     -                  active sync
  ATA-WDC-WD201KFGX-68     /dev/sde   ..HMGL4R   20.0TB   HDD    20k    41°C     -                  active sync
  ATA-WDC-WD201KFGX-68     /dev/sdf   ..HMGN0R   20.0TB   HDD    20k    40°C     -                  active sync
  ATA-WDC-WD201KFGX-68     /dev/sdg   ..J8DLGL   20.0TB   HDD    11k    39°C     -                  active sync
  ATA-WDC-WD202KFGX-68     /dev/sdh   ..GKA8GH   20.0TB   HDD    1k     38°C     -                  active sync

■ Standalone Disks
  Model                    Device     Serial     Size     Type   POH    Temp     Errors             State
  ATA-CT240BX500SSD1       /dev/sda   ..C29962   240GB    SSD    1k     42°C     -                  
  ATA-CT240BX500SSD1       /dev/sdb   ..C29A3F   240GB    SSD    1k     39°C     -                  

Last updated: 2026-11-02 05:03:00
```

---

## Requirements

- Python 3.7+ (uses Python standard library only; no external `pip` packages required)
- Linux system binaries: `mdadm`, `parted`, `smartctl` (`smartmontools`), `lsblk`

```bash
sudo apt update && sudo apt install -y mdadm parted smartmontools util-linux python3
```

---

## Usage

```bash
# Launch interactive TUI (auto-elevates via sudo)
raid

# Print static output for scripts / cron / MOTD
raid --static

# Direct CLI scrub actions
raid --pause
raid --resume
raid --check-full
raid --stop-check

# Non-root metadata flags
raid --help
raid --version
```

---

## License

GPL-3.0 License.
