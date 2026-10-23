# Changelog

All notable changes to the **RAID Health Monitor** (`raid.py`) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
