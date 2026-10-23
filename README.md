# RAID Health Monitor (`raid`) (v15.0.0)

A lightweight, real-time command-line health monitor for Linux **MDADM software RAID arrays** and physical drives.

Combines array-level status from `/proc/mdstat` and `mdadm` with drive-level SMART metrics (`smartctl`) and disk attributes (`parted`, `lsblk`, `/sys/block`) into a single color-coded terminal view.

---

## Features

- **Array Telemetry**:
  - Live array health state (`CLEAN`, `ACTIVE`, `DEGRADED`, `REBUILDING`, `CHECKING`).
  - Active and total device counts (`active/total`).
  - Mount point usage bars with percentage, used space, and total capacity.
  - Parity scrub / rebuild progress with ETA calculation.
  - Anomaly and mismatch sector counter (`mismatch_cnt`).
- **Physical Drive & SMART Telemetry**:
  - Hardware model, device node, and truncated serial number.
  - Drive capacity and storage media type (`SSD` / `HDD` via kernel rotational queue).
  - Power-On Hours (`POH`) and operational temperature (`°C`) with color alerts.
  - Real-time SMART sector error tracking:
    - Current Pending Sectors (`pend`)
    - Offline Uncorrectable Sectors (`uncorr`)
    - Reallocated Sector Count (`realloc`)
  - Array member state (`active sync`, `spare`, `faulty`).
- **Standalone / Non-RAID Disks**:
  - Automatically identifies and reports attached standalone disks (e.g., boot/OS drives) not enrolled in RAID arrays.

---

## Sample Output

```text
■ CLEAN  /dev/md0  raid5    6/6 devices    Usage: ▰▰▰▰▱▱▱▱▱▱ 47% (43T/91T)     (/mnt/storage)
  Model                    Device     Serial     Size     Type   POH    Temp     Errors             State
  WDC-WD201KFGX            /dev/sdc   ..HMGLER   20.0TB   HDD    20k    39°C     -                  active sync
  WDC-WD202KFGX            /dev/sdd   ..GH57LH   20.0TB   HDD    810    39°C     -                  active sync
  WDC-WD201KFGX            /dev/sde   ..HMGL4R   20.0TB   HDD    20k    39°C     -                  active sync
  WDC-WD201KFGX            /dev/sdf   ..HMGN0R   20.0TB   HDD    20k    39°C     -                  active sync
  WDC-WD201KFGX            /dev/sdg   ..J8DLGL   20.0TB   HDD    10k    38°C     -                  active sync
  WDC-WD202KFGX            /dev/sdh   ..GKA8GH   20.0TB   HDD    810    37°C     -                  active sync

■ Standalone Disks
  Model                    Device     Serial     Size     Type   POH    Temp     Errors             State
  CT240BX500SSD1           /dev/sda   ..C29962   240GB    SSD    1k     40°C     -                  
  CT240BX500SSD1           /dev/sdb   ..C29A3F   240GB    SSD    1k     40°C     -                  

Last updated: 2026-10-23 03:20:10
```

---

## Requirements

- Python 3.7+
- Linux system binaries:
  - `mdadm`
  - `parted`
  - `smartctl` (`smartmontools` package)
  - `lsblk`

Install required dependencies on Debian / Ubuntu:
```bash
sudo apt update && sudo apt install -y mdadm parted smartmontools util-linux python3
```

---

## Usage

Because SMART queries and low-level device inspections require elevated kernel privileges, run with `sudo`:

```bash
sudo ./raid.py
```

Display version or help without root:
```bash
./raid.py --help
./raid.py --version
```

---

## License

GPL-3.0 License.
