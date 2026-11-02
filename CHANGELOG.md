# Changelog

All notable changes to the **RAID Health Monitor** (`raid.py`) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [16.0.0] - 2026-11-02

### Added
- **Interactive Arrow-Key Terminal UI (TUI)**:
  - Navigate seamlessly up and down (`↑` / `↓` or `k` / `j`) across all MDADM RAID arrays and physical drives (RAID members and standalone disks) using an alternate screen buffer with zero external dependencies.
  - Live background refresh of RAID scrub/sync progress, speed, and sector counters every 2 seconds without interrupting keyboard navigation or re-polling slow `smartctl` queries.
- **Interactive RAID Action & Control Modal (`Enter` on any RAID array)**:
  - **Pause Check / Scrub**: Reads current `sync_completed` sector offset, sets `/sys/block/mdX/md/sync_action` to `idle`, and saves the exact sector position into `/sys/block/mdX/md/sync_min`.
  - **Resume Check / Scrub**: Resumes parity check from the saved `sync_min` sector offset.
  - **Start Full Check from 0%**: Resets `sync_min` to `0`, `sync_max` to `max`, and starts a full parity scrub from the beginning (with `[y/N]` confirmation prompt if a check is already underway or paused).
  - **Stop & Reset Check**: Sets `sync_action` to `idle` and clears `sync_min` back to `0`.
  - **Parity Repair Scrub**: Triggers `repair` mode via `sync_action`.
  - **Scrub Speed Profiles**: One-click speed presets (`Quiet: 50 MB/s max`, `System Default: 200 MB/s max`, `Turbo: 1,000 MB/s max`) modifying `sync_speed_min` and `sync_speed_max`.
  - **Raw Detail Toggle**: View complete `mdadm --detail` output inline inside the modal.
- **Expandable Drive SMART & Hardware Inspector (`Enter` on any Drive, or `a` for all)**:
  - Expands an inline detail drawer showing full untruncated Model and Serial Number, Firmware Version, SMART overall health status, Rotation Rate, Form Factor, SATA/Interface link speed, exact Power-On Hours (with years/days calculation), Power Cycle Count, Load Cycle Count, MD array slot/kernel state/error counter (or `lsblk` partitions/mounts for standalone drives), critical SMART attribute table, and latest SMART self-test result.
- **Static Mode (`-s` / `--static`) & Non-TTY Auto-Detection**:
  - Added `-s` / `--static` flag for scripts, cron jobs, and MOTD generators (`/etc/update-motd.d/95-raid`).
  - Automatically falls back to static output when `stdin` or `stdout` is not a TTY (e.g., `raid > /home/hu5h/raid.out`).
- **Direct CLI Action Flags**:
  - Added `--pause [md]`, `--resume [md]`, `--check-full [md]`, and `--stop-check [md]` for non-interactive CLI control.

### Fixed
- **Scrub Progress Display on Healthy Arrays**: Fixed an issue where active `CHECKING` scrub progress, speed, and ETA were omitted from single-line healthy array headers in static output.
- **Paused Check Visibility**: Arrays with a paused scrub (`sync_action == 'idle'` and `sync_min > 0`) now display `■ PAUSED` along with the saved scrub percentage and progress bar in both static and interactive modes.

---

## [15.0.1] - 2026-10-23

### Added
- **Privilege Auto-Elevation**: Automatically elevates with `sudo` when run by an unprivileged user, resolving the executable's absolute path and preserving CLI arguments.
- **Secure-Path Provisioning**: Automatically provisions `/usr/local/bin/raid -> /mnt/shin6/git/raid/raid.py` upon root elevation so `sudo raid` is immediately available in restricted `secure_path` environments.

---

## [15.0.0] - 2026-10-23

### Added
- **Git Repository Migration**: Formalized repository structure under version control.
- **CLI Flags**: Added `-v` / `--version` and `-h` / `--help` flags for non-root query capabilities.
- **Documentation**: Added comprehensive `README.md` and `pyproject.toml` configuration.
- **Array Telemetry**: MDADM software RAID state tracking (`CLEAN`, `ACTIVE`, `DEGRADED`, `REBUILDING`, `CHECKING`), RAID level, device count (`active/total`), mismatch counts (`mismatch_cnt`), mount point usage bars, and live rebuild/resync/scrub progress with ETA estimation.
- **Drive SMART Diagnostics**: Automated SMART query via `smartctl` for drive Model, Serial, Size, Media Type (`SSD` / `HDD` via rotational queue), Power-On Hours (`POH`), Temperature (`°C`), Sector error tracking (`Current_Pending_Sector`, `Offline_Uncorrectable`, `Reallocated_Sector_Ct`), and array membership state (`active sync`, `spare`, `faulty`).
- **Standalone Disk Telemetry**: Enumeration and health monitoring for non-RAID disks and OS drives.
