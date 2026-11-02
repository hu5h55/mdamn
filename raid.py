#!/usr/bin/env python3

################################################################################
# RAID Health Monitor
# Version: 16.0.0 - Python (Interactive TUI + RAID Actions + Static Mode)
################################################################################

VERSION = "16.0.0"

import os
import re
import sys
import json
import time
import tty
import termios
import select
import shutil
import signal
import subprocess
from datetime import datetime, timedelta

################################################################################
# Colors & Styling
################################################################################

RED       = '\033[38;5;203m'
GREEN     = '\033[38;5;150m'
YELLOW    = '\033[38;5;221m'
BLUE      = '\033[38;5;111m'
CYAN      = '\033[38;5;117m'
MAGENTA   = '\033[38;5;183m'
GRAY      = '\033[38;5;245m'
DARK_GRAY = '\033[38;5;238m'
WHITE     = '\033[38;5;255m'
BG_SEL    = '\033[48;5;236m'
BOLD      = '\033[1m'
DIM       = '\033[2m'
RESET     = '\033[0m'

ANSI_RE   = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


def strip_ansi(s):
    return ANSI_RE.sub('', s)


################################################################################
# System & Privilege Helpers
################################################################################

def run(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return r.stdout
    except Exception:
        return ''


def read_sysfs(path, default=''):
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except OSError:
        return default


def write_sysfs(path, value):
    with open(path, 'w') as f:
        f.write(f'{value}\n')


def kb_to_human(kb):
    try:
        kb = int(kb)
    except (ValueError, TypeError):
        return '?'
    if kb > 1073741824:
        return f'{kb / 1073741824:.2f} TiB'
    elif kb > 1048576:
        return f'{kb / 1048576:.2f} GiB'
    return f'{kb / 1024:.2f} MiB'


def usage_bar(pct, width=10, fill_col=None):
    pct   = max(0.0, min(100.0, float(pct)))
    fill  = int(round(pct * width / 100.0))
    empty = width - fill
    if fill_col is None:
        fill_col = RED if pct > 80 else YELLOW if pct > 60 else GREEN
    return f'{fill_col}{"▰" * fill}{GRAY}{"▱" * empty}{RESET}'


def check_privileges():
    if os.geteuid() != 0:
        script_path = sys.argv[0]
        if not os.path.isabs(script_path):
            script_path = shutil.which(script_path) or os.path.abspath(__file__)
        else:
            script_path = os.path.abspath(script_path)

        if not shutil.which('sudo'):
            print(f'\n{RED}ERROR: Root privileges required and sudo is not available.{RESET}\n', file=sys.stderr)
            sys.exit(1)

        try:
            os.execvp('sudo', ['sudo', script_path] + sys.argv[1:])
        except Exception as e:
            print(f'\n{RED}ERROR: Root privileges required.{RESET}', file=sys.stderr)
            print(f'Re-run with: {WHITE}sudo {script_path}{RESET} ({e})\n', file=sys.stderr)
            sys.exit(1)
    else:
        usr_bin = '/usr/local/bin/raid'
        target = os.path.realpath(__file__)
        if not os.path.exists(usr_bin) or os.path.realpath(usr_bin) != target:
            try:
                if os.path.islink(usr_bin) or os.path.exists(usr_bin):
                    os.remove(usr_bin)
                os.symlink(target, usr_bin)
            except OSError:
                pass


def check_deps():
    for cmd in ('mdadm', 'parted', 'smartctl', 'lsblk'):
        if not shutil.which(cmd):
            print(f'{RED}ERROR: Missing system dependency: {cmd}{RESET}', file=sys.stderr)
            print(f'Install with: {WHITE}sudo apt install -y mdadm parted smartmontools util-linux{RESET}', file=sys.stderr)
            sys.exit(1)


################################################################################
# RAID Control Actions (Pause / Resume / Full Check / Speed)
################################################################################

def get_md_sysfs_dir(md):
    md_short = os.path.basename(md)
    return f'/sys/block/{md_short}/md'


def get_md_sync_telemetry(md):
    """Read detailed sync/check state from /sys/block/mdX/md/ and /proc/mdstat."""
    sys_dir        = get_md_sysfs_dir(md)
    md_short       = os.path.basename(md)
    sync_action    = read_sysfs(f'{sys_dir}/sync_action', 'idle')
    sync_completed = read_sysfs(f'{sys_dir}/sync_completed', 'none')
    sync_min_raw   = read_sysfs(f'{sys_dir}/sync_min', '0')
    sync_max_raw   = read_sysfs(f'{sys_dir}/sync_max', 'max')
    comp_size_raw  = read_sysfs(f'{sys_dir}/component_size', '0')
    sync_speed_raw = read_sysfs(f'{sys_dir}/sync_speed', '0')
    speed_min_raw  = read_sysfs(f'{sys_dir}/sync_speed_min', '')
    speed_max_raw  = read_sysfs(f'{sys_dir}/sync_speed_max', '')
    mismatch_raw   = read_sysfs(f'{sys_dir}/mismatch_cnt', '0')
    level          = read_sysfs(f'{sys_dir}/level', '')
    array_state    = read_sysfs(f'{sys_dir}/array_state', '')
    degraded_raw   = read_sysfs(f'{sys_dir}/degraded', '0')

    try:
        comp_kb = int(comp_size_raw)
    except ValueError:
        comp_kb = 0
    total_sectors = comp_kb * 2  # 512-byte sectors per component

    try:
        sync_min = int(sync_min_raw)
    except ValueError:
        sync_min = 0

    try:
        mismatch = int(mismatch_raw)
    except ValueError:
        mismatch = 0

    try:
        degraded = int(degraded_raw)
    except ValueError:
        degraded = 0

    try:
        sync_speed_kb = int(sync_speed_raw.split()[0])
    except (ValueError, IndexError):
        sync_speed_kb = 0

    cur_sectors = 0
    m_comp = re.match(r'(\d+)\s*/\s*(\d+)', sync_completed)
    if m_comp:
        cur_sectors   = int(m_comp.group(1))
        total_sectors = int(m_comp.group(2))
    elif sync_action == 'idle' and sync_min > 0:
        cur_sectors = sync_min

    pct = (cur_sectors / total_sectors * 100.0) if total_sectors > 0 else 0.0

    # Parse /proc/mdstat for finish ETA and speed fallback
    mdstat_op     = None
    mdstat_pct    = None
    mdstat_finish = None
    mdstat_speed  = None
    try:
        mdstat_lines = open('/proc/mdstat').read().splitlines()
        for i, ln in enumerate(mdstat_lines):
            if ln.startswith(md_short + ' '):
                block = '\n'.join(mdstat_lines[i:i+4])
                sm = re.search(
                    r'(recovery|resync|check|reshape|repair)\s*=\s*(\S+).*?finish=(\S+)\s+speed=(\S+)',
                    block
                )
                if sm:
                    mdstat_op     = sm.group(1)
                    mdstat_pct    = sm.group(2)
                    mdstat_finish = sm.group(3)
                    mdstat_speed  = sm.group(4)
                break
    except OSError:
        pass

    is_paused = (sync_action in ('idle', 'frozen') and sync_min > 0 and total_sectors > 0 and sync_min < total_sectors)

    return {
        'sync_action':    sync_action,
        'sync_completed': sync_completed,
        'sync_min':       sync_min,
        'sync_max':       sync_max_raw,
        'cur_sectors':    cur_sectors,
        'total_sectors':  total_sectors,
        'pct':            pct,
        'is_paused':      is_paused,
        'sync_speed_kb':  sync_speed_kb,
        'speed_min_raw':  speed_min_raw,
        'speed_max_raw':  speed_max_raw,
        'mismatch':       mismatch,
        'degraded':       degraded,
        'level':          level,
        'array_state':    array_state,
        'mdstat_op':      mdstat_op,
        'mdstat_pct':     mdstat_pct,
        'mdstat_finish':  mdstat_finish,
        'mdstat_speed':   mdstat_speed,
    }


def action_pause_check(md):
    """Pause an active RAID check/scrub and preserve current sector offset in sync_min."""
    sys_dir = get_md_sysfs_dir(md)
    tel     = get_md_sync_telemetry(md)

    if tel['sync_action'] == 'idle' and tel['is_paused']:
        return True, f"{md}: Check is already paused at {tel['pct']:.1f}% (sector {tel['sync_min']:,})."

    if tel['sync_action'] not in ('check', 'repair', 'resync'):
        return False, f"{md}: No active check/scrub to pause (current sync_action: {tel['sync_action']})."

    saved_sector = tel['cur_sectors']
    try:
        write_sysfs(f'{sys_dir}/sync_action', 'idle')
        if saved_sector > 0:
            write_sysfs(f'{sys_dir}/sync_min', str(saved_sector))
        tel_after = get_md_sync_telemetry(md)
        return True, (
            f"{md}: Scrub paused at {tel_after['pct']:.1f}% "
            f"(sector {tel_after['sync_min']:,} / {tel_after['total_sectors']:,})."
        )
    except OSError as e:
        return False, f"{md}: Failed to pause check: {e}"


def action_resume_check(md):
    """Resume RAID check from current sync_min offset (or start if sync_min == 0)."""
    sys_dir = get_md_sysfs_dir(md)
    tel     = get_md_sync_telemetry(md)

    if tel['sync_action'] in ('check', 'repair', 'resync', 'recover'):
        return True, f"{md}: {tel['sync_action'].upper()} is already running ({tel['pct']:.1f}%)."

    try:
        if tel['sync_max'] != 'max':
            write_sysfs(f'{sys_dir}/sync_max', 'max')
        write_sysfs(f'{sys_dir}/sync_action', 'check')
        time.sleep(0.15)
        tel_after = get_md_sync_telemetry(md)
        if tel['sync_min'] > 0:
            return True, (
                f"{md}: Resumed check from {tel['pct']:.1f}% "
                f"(sector {tel['sync_min']:,} / {tel['total_sectors']:,})."
            )
        return True, f"{md}: Started check from 0%."
    except OSError as e:
        return False, f"{md}: Failed to resume check: {e}"


def action_start_full_check(md):
    """Reset sync_min to 0 and start a full parity check from the beginning."""
    sys_dir = get_md_sysfs_dir(md)
    tel     = get_md_sync_telemetry(md)

    try:
        if tel['sync_action'] != 'idle':
            write_sysfs(f'{sys_dir}/sync_action', 'idle')
            time.sleep(0.1)
        write_sysfs(f'{sys_dir}/sync_min', '0')
        write_sysfs(f'{sys_dir}/sync_max', 'max')
        write_sysfs(f'{sys_dir}/sync_action', 'check')
        time.sleep(0.15)
        return True, f"{md}: Reset sync_min=0 and started full parity check from 0.0%."
    except OSError as e:
        return False, f"{md}: Failed to start full check: {e}"


def action_stop_reset_check(md):
    """Stop any active check and reset sync_min to 0."""
    sys_dir = get_md_sysfs_dir(md)
    try:
        write_sysfs(f'{sys_dir}/sync_action', 'idle')
        write_sysfs(f'{sys_dir}/sync_min', '0')
        write_sysfs(f'{sys_dir}/sync_max', 'max')
        return True, f"{md}: Stopped check and reset saved position (sync_min=0)."
    except OSError as e:
        return False, f"{md}: Failed to stop/reset check: {e}"


def action_start_repair(md):
    """Start a repair scrub on the array."""
    sys_dir = get_md_sysfs_dir(md)
    tel     = get_md_sync_telemetry(md)
    try:
        if tel['sync_action'] != 'idle':
            write_sysfs(f'{sys_dir}/sync_action', 'idle')
            time.sleep(0.1)
        write_sysfs(f'{sys_dir}/sync_action', 'repair')
        time.sleep(0.15)
        return True, f"{md}: Started parity repair scrub."
    except OSError as e:
        return False, f"{md}: Failed to start repair scrub: {e}"


def action_set_speed(md, min_kb, max_kb, label):
    """Set sync_speed_min and sync_speed_max on the array."""
    sys_dir = get_md_sysfs_dir(md)
    try:
        write_sysfs(f'{sys_dir}/sync_speed_min', str(min_kb))
        write_sysfs(f'{sys_dir}/sync_speed_max', str(max_kb))
        return True, f"{md}: Set scrub speed profile to {label} ({min_kb:,} – {max_kb:,} KB/s)."
    except OSError as e:
        return False, f"{md}: Failed to set speed limits: {e}"


################################################################################
# Info Gathering
################################################################################

def format_eta(min_str):
    try:
        minutes  = float(min_str.replace('min', ''))
        days     = int(minutes // 1440)
        hours    = int((minutes % 1440) // 60)
        mins_rem = int(minutes % 60)
        eta_date = datetime.now() + timedelta(minutes=minutes)
        eta_str  = eta_date.strftime("%b %d, %H:%M")

        if days > 0:
            return f"{days}d {hours}h (ETA: {eta_str})"
        elif hours > 0:
            return f"{hours}h {mins_rem}m (ETA: {eta_str})"
        return f"{mins_rem}m (ETA: {eta_str})"
    except ValueError:
        return min_str


def get_raids():
    raids = []
    try:
        for line in open('/proc/mdstat'):
            if re.match(r'^md\d+\s*:', line):
                raids.append('/dev/' + line.split(':')[0].strip())
    except OSError:
        pass
    return raids


def get_all_devs(parted_out):
    """Only return physical block devices from parted Disk lines — no md, no mapper."""
    devs = []
    for line in parted_out.splitlines():
        m = re.match(r'^Disk\s+(/dev/(?!md|mapper)[^:\s]+):', line)
        if m:
            devs.append(m.group(1))
    return devs


def get_disk_type(dev):
    try:
        rot = open(f'/sys/block/{os.path.basename(dev)}/queue/rotational').read().strip()
        return 'HDD' if rot == '1' else 'SSD'
    except OSError:
        return 'UNK'


def get_lsblk_map():
    """Return mapping of '/dev/sdX' -> lsblk dict with partitions, mounts, transport."""
    out = run('lsblk -J -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL,REV,ROTA,TRAN 2>/dev/null')
    dev_map = {}
    try:
        data = json.loads(out)
        for blk in data.get('blockdevices', []):
            name = blk.get('name', '')
            if name:
                dev_map[f'/dev/{name}'] = blk
    except Exception:
        pass
    return dev_map


def get_disks_from_parted(parted_out, filter_devs):
    """
    Parse parted -l output into list of dicts: {model, dev, size, type}.
    Matches by base device name (trailing digits stripped) so /dev/sdb1 -> /dev/sdb.
    """
    disks      = []
    model      = None
    filter_set = {re.sub(r'\d+$', '', d) for d in filter_devs}

    for line in parted_out.splitlines():
        if re.search(r'Warning|Error|read-only', line):
            continue
        m = re.match(r'^Model:\s+(.+)', line)
        if m:
            raw   = re.sub(r'\s*\(.*?\)\s*$', '', m.group(1)).strip()
            model = re.sub(r'\s+', '-', raw)
            continue
        m = re.match(r'^Disk\s+(/dev/\S+?):\s+(\S+)', line)
        if m and model:
            dev  = m.group(1).rstrip(':')
            size = m.group(2)
            if dev in filter_set:
                disks.append({
                    'model': model,
                    'dev':   dev,
                    'size':  size,
                    'type':  get_disk_type(dev),
                })
            model = None

    return disks


def get_smart_info(dev):
    out  = run(f'smartctl --all {dev} 2>/dev/null')
    info = {
        'temp':         0,
        'uncorr':       0,
        'pend':         0,
        'realloc':      0,
        'poh':          0,
        'serial':       'UNK',
        'firmware':     'UNK',
        'health':       'UNK',
        'rotation':     'UNK',
        'form_factor':  'UNK',
        'sata_ver':     'UNK',
        'sector_size':  'UNK',
        'power_cycles': 0,
        'load_cycles':  0,
        'start_stop':   0,
        'udma_crc':     0,
        'attrs':        [],
        'selftests':    [],
        'raw_out':      out,
    }

    for line in out.splitlines():
        s = line.strip()
        f = s.split()
        def col(n):
            try: return f[n]
            except IndexError: return ''

        if line.startswith('Serial Number:') and info['serial'] == 'UNK':
            info['serial'] = line.split(':', 1)[1].strip()
        elif line.startswith('Firmware Version:'):
            info['firmware'] = line.split(':', 1)[1].strip()
        elif line.startswith('Rotation Rate:'):
            info['rotation'] = line.split(':', 1)[1].strip()
        elif line.startswith('Form Factor:'):
            info['form_factor'] = line.split(':', 1)[1].strip()
        elif line.startswith('SATA Version is:'):
            info['sata_ver'] = line.split(':', 1)[1].strip()
        elif line.startswith('Sector Sizes:') or line.startswith('Sector Size:'):
            info['sector_size'] = line.split(':', 1)[1].strip()
        elif 'overall-health self-assessment test result:' in line.lower():
            info['health'] = line.split(':', 1)[1].strip()
        elif line.startswith('SMART Health Status:'):
            info['health'] = line.split(':', 1)[1].strip()

        # Parse SMART attribute table rows: ID# ATTRIBUTE_NAME FLAG VALUE WORST THRESH TYPE UPDATED WHEN_FAILED RAW_VALUE
        if len(f) >= 10 and f[0].isdigit() and re.match(r'^0x[0-9a-fA-F]+$', f[2]):
            attr_id   = int(f[0])
            attr_name = f[1]
            val       = f[3]
            worst     = f[4]
            thresh    = f[5]
            raw_str   = ' '.join(f[9:])
            raw_int_m = re.match(r'^(\d+)', f[9])
            raw_int   = int(raw_int_m.group(1)) if raw_int_m else 0

            info['attrs'].append({
                'id':     attr_id,
                'name':   attr_name,
                'value':  val,
                'worst':  worst,
                'thresh': thresh,
                'raw':    raw_str,
                'raw_int': raw_int,
            })

            al = attr_name.lower()
            if 'temperature_celsius' in al or 'airflow_temperature' in al:
                if not info['temp'] and raw_int > 0:
                    info['temp'] = raw_int
            elif 'offline_uncorrectable' in al:
                info['uncorr'] = raw_int
            elif 'current_pending_sector' in al:
                info['pend'] = raw_int
            elif 'reallocated_sector_ct' in al:
                info['realloc'] = raw_int
            elif 'power_on_hours' in al:
                info['poh'] = raw_int
            elif 'power_cycle_count' in al:
                info['power_cycles'] = raw_int
            elif 'load_cycle_count' in al:
                info['load_cycles'] = raw_int
            elif 'start_stop_count' in al:
                info['start_stop'] = raw_int
            elif 'udma_crc_error_count' in al:
                info['udma_crc'] = raw_int

        # Fallback for NVMe / non-standard output lines
        if re.search(r'Power On Hours', line, re.I) and not info['poh']:
            try: info['poh'] = int(f[-1].replace(',', ''))
            except ValueError: pass
        elif re.search(r'^Temperature:\s+(\d+)\s+Celsius', line, re.I) and not info['temp']:
            m_t = re.search(r'(\d+)\s+Celsius', line, re.I)
            if m_t:
                info['temp'] = int(m_t.group(1))

        # SMART self-test log lines: "# 1  Short offline       Completed without error       00%     20142         -"
        if s.startswith('#') and len(f) >= 6 and f[1].isdigit():
            info['selftests'].append(s)

    return info


def get_member_states(md_info):
    """Return dict of {'/dev/sdX': 'active sync'} from mdadm --detail output."""
    states = {}
    for line in md_info.splitlines():
        m = re.search(r'((?:active|spare|faulty|rebuild|resync)[^/]*)(/dev/(?!md)\S+)', line)
        if m:
            state = m.group(1).strip()
            dev   = m.group(2).strip()
            states[dev] = state
            states[re.sub(r'\d+$', '', dev)] = state
    return states


def filter_devs(all_devs, remove):
    remove_bases = {re.sub(r'\d+$', '', d) for d in remove}
    return [d for d in all_devs if re.sub(r'\d+$', '', d) not in remove_bases]


def parse_df_for_md(md, df_out):
    mount_str = ''
    usage_str = ''
    mount_pt  = ''
    pct_val   = 0
    used_str  = ''
    tot_str   = ''
    for line in df_out.splitlines():
        if md in line:
            parts = line.split()
            if len(parts) >= 6:
                mount_pt  = parts[5]
                mount_str = f' {GRAY}({mount_pt}){RESET}'
                pct_s     = parts[4].rstrip('%')
                used_str  = parts[2]
                tot_str   = parts[1]
                if pct_s.isdigit():
                    pct_val   = int(pct_s)
                    usage_str = f'{usage_bar(pct_val)} {pct_val}% {GRAY}({used_str}/{tot_str}){RESET}'
            break
    return {
        'mount_pt':  mount_pt,
        'mount_str': mount_str,
        'usage_str': usage_str,
        'pct':       pct_val,
        'used':      used_str,
        'total':     tot_str,
    }


def parse_raid_summary(md, md_info, df_out):
    def field(label):
        for line in md_info.splitlines():
            if label in line and ':' in line:
                return line.split(':', 1)[1].strip()
        return ''

    level      = field('Raid Level')
    total      = field('Total Devices')
    active     = field('Active Devices')
    working    = field('Working Devices')
    failed     = field('Failed Devices')
    spare      = field('Spare Devices')
    state      = field('State :')
    layout     = field('Layout')
    chunk_size = field('Chunk Size')
    uuid       = field('UUID')
    events     = field('Events')

    m_size = re.search(r'Array Size\s*:\s*(\d+)', md_info)
    size   = kb_to_human(m_size.group(1)) if m_size else '?'

    m_used = re.search(r'Used Dev Size\s*:\s*(\d+)', md_info)
    dev_sz = kb_to_human(m_used.group(1)) if m_used else '?'

    df_info  = parse_df_for_md(md, df_out)
    sync_tel = get_md_sync_telemetry(md)
    mismatch = sync_tel['mismatch']

    su = state.upper()
    if   'DEGRADED' in su: state_col, su = RED,    'DEGRADED'
    elif 'ACTIVE'   in su: state_col, su = GREEN,  'ACTIVE'
    elif 'CLEAN'    in su: state_col, su = CYAN,   'CLEAN'
    else:                  state_col     = GRAY

    sync_str = ''
    op = sync_tel['mdstat_op'] or (
        sync_tel['sync_action'] if sync_tel['sync_action'] not in ('idle', '') else None
    )

    if op:
        pct_display = sync_tel['mdstat_pct'] or f"{sync_tel['pct']:.1f}%"
        eta_display = format_eta(sync_tel['mdstat_finish']) if sync_tel['mdstat_finish'] else ''
        spd_mb      = f"{sync_tel['sync_speed_kb'] / 1024:.1f} MB/s" if sync_tel['sync_speed_kb'] else ''
        if op == 'check':
            state_col, su = CYAN, 'CHECKING'
            extra = ' · '.join(x for x in (spd_mb, eta_display) if x)
            sync_str = f'    {GRAY}Scrub:{RESET} {usage_bar(sync_tel["pct"], 8, CYAN)} {CYAN}{pct_display}{RESET}'
            if extra:
                sync_str += f' {GRAY}({extra}){RESET}'
        elif op == 'repair':
            state_col, su = YELLOW, 'REPAIRING'
            extra = ' · '.join(x for x in (spd_mb, eta_display) if x)
            sync_str = f'    {GRAY}Repair:{RESET} {usage_bar(sync_tel["pct"], 8, YELLOW)} {YELLOW}{pct_display}{RESET}'
            if extra:
                sync_str += f' {GRAY}({extra}){RESET}'
        else:
            state_col, su = YELLOW, 'REBUILDING'
            extra = ' · '.join(x for x in (spd_mb, eta_display) if x)
            sync_str = f'    {GRAY}Sync:{RESET} {usage_bar(sync_tel["pct"], 8, YELLOW)} {YELLOW}{pct_display}{RESET}'
            if extra:
                sync_str += f' {GRAY}({extra}){RESET}'
    elif sync_tel['is_paused']:
        state_col, su = YELLOW, 'PAUSED'
        sync_str = (
            f'    {GRAY}Scrub:{RESET} {usage_bar(sync_tel["pct"], 8, YELLOW)} '
            f'{YELLOW}{sync_tel["pct"]:.1f}% paused{RESET}'
        )

    anomaly = (
        mismatch != 0
        or su in ('DEGRADED', 'REBUILDING', 'REPAIRING')
        or (active.isdigit() and total.isdigit() and active != total)
    )

    return {
        'md':         md,
        'md_info':    md_info,
        'level':      level,
        'total':      total,
        'active':     active,
        'working':    working,
        'failed':     failed,
        'spare':      spare,
        'state_raw':  state,
        'su':         su,
        'state_col':  state_col,
        'size':       size,
        'dev_size':   dev_sz,
        'layout':     layout,
        'chunk_size': chunk_size,
        'uuid':       uuid,
        'events':     events,
        'mismatch':   mismatch,
        'df':         df_info,
        'sync_tel':   sync_tel,
        'sync_str':   sync_str,
        'anomaly':    anomaly,
    }


def collect_full_telemetry():
    """Gather all RAID arrays, member drives, standalone drives, and SMART attributes."""
    parted_out = run('parted -l 2>&1')
    df_out     = run('df -hP 2>/dev/null')
    lsblk_map  = get_lsblk_map()
    all_devs   = get_all_devs(parted_out)
    raids      = get_raids()
    non_raid   = list(all_devs)

    raid_list = []
    for md in raids:
        md_info = run(f'mdadm --detail {md} 2>/dev/null')
        if not md_info.strip():
            continue

        r_sum    = parse_raid_summary(md, md_info, df_out)
        members  = re.findall(r'/dev/(?!md)\S+', md_info)
        non_raid = filter_devs(non_raid, members)

        member_states = get_member_states(md_info)
        disk_list     = get_disks_from_parted(parted_out, members) if members else []
        sys_dir       = get_md_sysfs_dir(md)

        enriched_disks = []
        for d in disk_list:
            smart     = get_smart_info(d['dev'])
            state_txt = member_states.get(d['dev'], '')
            # Find exact partition member (e.g. sdc1) for sysfs slot/errors
            dev_base  = os.path.basename(d['dev'])
            part_name = next(( os.path.basename(m) for m in members if os.path.basename(m).startswith(dev_base) ), dev_base)
            slot_raw  = read_sysfs(f'{sys_dir}/dev-{part_name}/slot', '-')
            err_raw   = read_sysfs(f'{sys_dir}/dev-{part_name}/errors', '0')
            st_sysfs  = read_sysfs(f'{sys_dir}/dev-{part_name}/state', '')

            enriched_disks.append({
                **d,
                'smart':      smart,
                'state_txt':  state_txt,
                'raid_md':    md,
                'member_dev': f'/dev/{part_name}',
                'slot':       slot_raw,
                'md_errors':  err_raw,
                'md_state':   st_sysfs,
                'lsblk':      lsblk_map.get(d['dev'], {}),
            })

        r_sum['disks'] = enriched_disks
        raid_list.append(r_sum)

    standalone_disks = []
    if non_raid:
        for d in get_disks_from_parted(parted_out, non_raid):
            smart = get_smart_info(d['dev'])
            standalone_disks.append({
                **d,
                'smart':      smart,
                'state_txt':  '',
                'raid_md':    None,
                'member_dev': d['dev'],
                'slot':       '-',
                'md_errors':  '0',
                'md_state':   '',
                'lsblk':      lsblk_map.get(d['dev'], {}),
            })

    return {
        'raids':      raid_list,
        'standalone': standalone_disks,
        'df_out':     df_out,
        'updated_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def refresh_raid_fast(telemetry):
    """Fast (<5ms) refresh of RAID sync/scrub progress without re-running smartctl."""
    df_out = telemetry.get('df_out') or run('df -hP 2>/dev/null')
    for i, r in enumerate(telemetry['raids']):
        md      = r['md']
        md_info = r['md_info']
        updated = parse_raid_summary(md, md_info, df_out)
        updated['disks'] = r['disks']
        telemetry['raids'][i] = updated
    telemetry['updated_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


################################################################################
# Formatting & Static Rendering
################################################################################

def format_raid_lines(r, selected=False, expanded=False):
    """Return list of formatted lines for a RAID array header."""
    sel_prefix = f'{BG_SEL}{CYAN}{BOLD}❯ {RESET}{BG_SEL}' if selected else '  '
    sel_suffix = f'{RESET}' if selected else ''

    state_col = r['state_col']
    su        = r['su']
    md        = r['md']
    level     = r['level']
    active    = r['active']
    total     = r['total']
    usage_str = r['df']['usage_str']
    mount_str = r['df']['mount_str']
    sync_str  = r['sync_str']
    mismatch  = r['mismatch']

    lines = []
    if not r['anomaly']:
        line1 = (
            f'{sel_prefix}{state_col}■ {su}{RESET}  {BLUE}{BOLD if selected else ""}{md}{RESET}  '
            f'{GRAY}{level}{RESET}    {GRAY}{active}/{total} devices{RESET}'
        )
        if usage_str:
            line1 += f'    {GRAY}Usage:{RESET} {usage_str}'
        if mount_str:
            line1 += f'   {mount_str}'
        if sync_str:
            line1 += sync_str
        if selected:
            line1 += f'  {CYAN}[Enter: Actions]{RESET}'
        line1 += sel_suffix
        lines.append(line1)
    else:
        line1 = f'{sel_prefix}{state_col}■ {su}{RESET}  {BLUE}{BOLD if selected else ""}{md}{RESET}  {GRAY}{level}{RESET}'
        if mount_str:
            line1 += f'    {mount_str}'
        if selected:
            line1 += f'  {CYAN}[Enter: Actions]{RESET}'
        line1 += sel_suffix
        lines.append(line1)

        detail = (
            f'    {YELLOW}!{RESET}    '
            f'{GRAY}Mismatch: {RED if mismatch else GREEN}{mismatch}{RESET}    '
            f'{GRAY}Active: {WHITE}{active}/{total}{RESET}'
        )
        if usage_str:
            detail += f'    {GRAY}Usage:{RESET} {usage_str}'
        if sync_str:
            detail += sync_str
        lines.append(detail)

    return lines


def format_disk_header(indent='  '):
    return (
        f'{indent}{BOLD}{WHITE}{"Model":<24} {"Device":<10} {"Serial":<10} {"Size":<8} '
        f'{"Type":<6} {"POH":<6} {"Temp":<8} {"Errors":<18} State{RESET}'
    )


def format_disk_row(d, selected=False, expanded=False, interactive=False):
    smart = d['smart']

    model = d['model']
    if len(model) > 23:
        model = model[:22] + '..'

    serial     = smart['serial']
    serial_fmt = ('..' + serial[-6:]) if len(serial) > 6 else serial

    poh     = smart['poh']
    poh_col = RED if poh > 50000 else YELLOW if poh > 30000 else GREEN if poh else GRAY
    poh_fmt = (f'{poh // 1000}k' if poh > 1000 else str(poh)) if poh else '0'

    temp     = smart['temp']
    temp_col = RED if temp >= 50 else YELLOW if temp >= 40 else GREEN if temp > 0 else GRAY
    temp_out = f'{temp}°C' if temp > 0 else 'N/A'

    errs = []
    if smart['pend']:    errs.append(f'Pend:{smart["pend"]}')
    if smart['uncorr']:  errs.append(f'Unc:{smart["uncorr"]}')
    if smart['realloc']: errs.append(f'Realloc:{smart["realloc"]}')
    err_str = ' '.join(errs) if errs else '-'
    err_col = RED if errs else GRAY

    type_col = CYAN if d['type'] == 'SSD' else YELLOW

    state_txt = d['state_txt']
    if   re.search(r'faulty|fail',           state_txt): state_col = RED
    elif re.search(r'spare',                  state_txt): state_col = BLUE
    elif re.search(r'rebuild|recover|resync', state_txt): state_col = YELLOW
    else:                                                 state_col = GRAY

    model_col = RED if RED in (poh_col, temp_col, err_col, state_col) else WHITE

    if not interactive:
        prefix = '  '
    else:
        arrow = '▾' if expanded else ('❯' if selected else ' ')
        prefix = f'{CYAN}{BOLD}{arrow}{RESET} ' if (selected or expanded) else '  '

    bg = BG_SEL if selected else ''
    hint = f' {CYAN}{"[Enter: Collapse]" if expanded else "[Enter: Details]"}{RESET}' if selected else ''

    return (
        f'{prefix}{bg}'
        f'{model_col}{BOLD if selected else ""}{model:<24}{RESET}{bg} '
        f'{CYAN}{d["dev"]:<10}{RESET}{bg} '
        f'{GRAY}{serial_fmt:<10}{RESET}{bg} '
        f'{WHITE}{d["size"]:<8}{RESET}{bg} '
        f'{type_col}{d["type"]:<6}{RESET}{bg} '
        f'{poh_col}{poh_fmt:<6}{RESET}{bg} '
        f'{temp_col}{temp_out:<8}{RESET}{bg} '
        f'{err_col}{err_str:<18}{RESET}{bg} '
        f'{state_col}{state_txt}{RESET}'
        f'{hint}'
    )


def format_disk_expanded(d):
    """Render an inline expanded box with full SMART and hardware details for a drive."""
    smart = d['smart']
    poh   = smart['poh']
    years = poh // 8760
    days  = (poh % 8760) // 24
    poh_detail = f"{poh:,} hrs ({years}y {days}d)" if poh else "0 hrs"

    health_col = GREEN if 'PASS' in smart['health'].upper() or 'OK' in smart['health'].upper() else (
        RED if 'FAIL' in smart['health'].upper() else YELLOW
    )

    lines = []
    lines.append(f'    {DARK_GRAY}╭─── {CYAN}{BOLD}{d["dev"]}{RESET} {GRAY}({d["model"]}){DARK_GRAY} ' + '─' * max(10, 68 - len(d['dev']) - len(d['model'])) + f'{RESET}')
    lines.append(
        f'    {DARK_GRAY}│{RESET} {GRAY}Serial:{RESET} {WHITE}{smart["serial"]:<20}{RESET} '
        f'{GRAY}Firmware:{RESET} {WHITE}{smart["firmware"]:<12}{RESET} '
        f'{GRAY}SMART Health:{RESET} {health_col}{BOLD}{smart["health"]}{RESET}'
    )
    lines.append(
        f'    {DARK_GRAY}│{RESET} {GRAY}Media:{RESET}  {WHITE}{smart["rotation"]:<20}{RESET} '
        f'{GRAY}Form:{RESET}     {WHITE}{smart["form_factor"]:<12}{RESET} '
        f'{GRAY}Interface:{RESET}    {WHITE}{smart["sata_ver"]}{RESET}'
    )
    lines.append(
        f'    {DARK_GRAY}│{RESET} {GRAY}Power:{RESET}  {WHITE}{poh_detail:<20}{RESET} '
        f'{GRAY}Cycles:{RESET}   {WHITE}{smart["power_cycles"]:<12}{RESET} '
        f'{GRAY}Load Cycles:{RESET}  {WHITE}{smart["load_cycles"]} (Start/Stop: {smart["start_stop"]}){RESET}'
    )

    if d['raid_md']:
        lines.append(
            f'    {DARK_GRAY}│{RESET} {GRAY}RAID:{RESET}   {BLUE}{d["raid_md"]}{RESET} '
            f'({CYAN}{d["member_dev"]}{RESET}, Slot {WHITE}{d["slot"]}{RESET}, '
            f'Kernel State: {GREEN if "sync" in d["md_state"] else YELLOW}{d["md_state"] or d["state_txt"]}{RESET}, '
            f'MD Read Errors: {RED if d["md_errors"] != "0" else GREEN}{d["md_errors"]}{RESET})'
        )
    elif d.get('lsblk', {}).get('children'):
        parts = []
        for ch in d['lsblk'].get('children', []):
            mp = ch.get('mountpoint') or ch.get('fstype') or ''
            parts.append(f"/dev/{ch.get('name')} ({ch.get('size')}{', ' + mp if mp else ''})")
        if parts:
            lines.append(f'    {DARK_GRAY}│{RESET} {GRAY}Mounts:{RESET} {WHITE}{" · ".join(parts)}{RESET}')

    # Critical SMART attributes table
    key_ids = {1, 5, 7, 9, 10, 12, 187, 188, 192, 193, 194, 196, 197, 198, 199, 200, 202, 231, 233}
    shown_attrs = [a for a in smart['attrs'] if a['id'] in key_ids or a['raw_int'] > 0][:10]
    if shown_attrs:
        lines.append(f'    {DARK_GRAY}│{RESET}')
        lines.append(
            f'    {DARK_GRAY}│{RESET} {BOLD}{GRAY}{"ID":<4} {"SMART Attribute":<28} '
            f'{"Val":<5} {"Wrst":<5} {"Thr":<5} {"Raw Value"}{RESET}'
        )
        for a in shown_attrs:
            is_err_attr = a['id'] in (5, 187, 188, 196, 197, 198, 199) and a['raw_int'] > 0
            row_col = RED if is_err_attr else WHITE
            lines.append(
                f'    {DARK_GRAY}│{RESET} {GRAY}{a["id"]:<4}{RESET} '
                f'{row_col}{a["name"]:<28}{RESET} '
                f'{GRAY}{a["value"]:<5} {a["worst"]:<5} {a["thresh"]:<5}{RESET} '
                f'{row_col}{a["raw"]}{RESET}'
            )

    if smart['selftests']:
        lines.append(f'    {DARK_GRAY}│{RESET} {GRAY}Last Self-Test:{RESET} {WHITE}{smart["selftests"][0]}{RESET}')

    lines.append(f'    {DARK_GRAY}╰' + '─' * 82 + f'{RESET}')
    return lines


def render_static(telemetry):
    """Print the classic static RAID & Disk report (used by --static and non-TTY callers)."""
    print()
    for r in telemetry['raids']:
        # In static mode, use clean 2-space indent so output matches v15 layout
        state_col = r['state_col']
        su        = r['su']
        md        = r['md']
        level     = r['level']
        active    = r['active']
        total     = r['total']
        usage_str = r['df']['usage_str']
        mount_str = r['df']['mount_str']
        sync_str  = r['sync_str']
        mismatch  = r['mismatch']

        if not r['anomaly']:
            line1 = f'{state_col}■ {su}{RESET}  {BLUE}{md}{RESET}  {GRAY}{level}{RESET}    {GRAY}{active}/{total} devices{RESET}'
            if usage_str:
                line1 += f'    {GRAY}Usage:{RESET} {usage_str}'
            if mount_str:
                line1 += f'    {mount_str}'
            if sync_str:
                line1 += sync_str
            print(line1)
        else:
            line1 = f'{state_col}■ {su}{RESET}  {BLUE}{md}{RESET}  {GRAY}{level}{RESET}'
            if mount_str:
                line1 += f'    {mount_str}'
            print(line1)
            detail = (
                f'  {YELLOW}!{RESET}    '
                f'{GRAY}Mismatch: {RED if mismatch else GREEN}{mismatch}{RESET}    '
                f'{GRAY}Active: {WHITE}{active}/{total}{RESET}'
            )
            if usage_str:
                detail += f'    {GRAY}Usage:{RESET} {usage_str}'
            if sync_str:
                detail += sync_str
            print(detail)

        if r['disks']:
            print(format_disk_header('  '))
            for d in r['disks']:
                print(format_disk_row(d, interactive=False))
        print()

    if telemetry['standalone']:
        print(f'{MAGENTA}■ Standalone Disks{RESET}')
        print(format_disk_header('  '))
        for d in telemetry['standalone']:
            print(format_disk_row(d, interactive=False))
        print()

    print(f'{GRAY}Last updated: {telemetry["updated_at"]}{RESET}\n')


################################################################################
# Interactive Terminal UI (Arrow Keys + Enter Actions + Expandable Details)
################################################################################

def read_key(timeout=2.0):
    """Read a single keypress or escape sequence from stdin with timeout."""
    fd = sys.stdin.fileno()
    rlist, _, _ = select.select([fd], [], [], timeout)
    if not rlist:
        return None
    ch = os.read(fd, 1).decode('utf-8', errors='ignore')
    if ch == '\x1b':
        rlist2, _, _ = select.select([fd], [], [], 0.04)
        if rlist2:
            ch2 = os.read(fd, 1).decode('utf-8', errors='ignore')
            if ch2 in ('[', 'O'):
                rlist3, _, _ = select.select([fd], [], [], 0.04)
                if rlist3:
                    ch3 = os.read(fd, 1).decode('utf-8', errors='ignore')
                    if ch3 == 'A': return 'UP'
                    if ch3 == 'B': return 'DOWN'
                    if ch3 == 'C': return 'RIGHT'
                    if ch3 == 'D': return 'LEFT'
                    if ch3 in ('5', '6'):
                        os.read(fd, 1)
                        return 'PGUP' if ch3 == '5' else 'PGDN'
        return 'ESC'
    elif ch in ('\r', '\n'):
        return 'ENTER'
    elif ch == ' ':
        return 'SPACE'
    elif ch in ('\x7f', '\x08'):
        return 'BACKSPACE'
    elif ch == '\x03':
        return 'CTRL_C'
    return ch


def build_selectable_items(telemetry):
    """Build ordered list of selectable items: ('raid', raid_dict) and ('disk', disk_dict)."""
    items = []
    for r in telemetry['raids']:
        items.append(('raid', r['md'], r))
        for d in r['disks']:
            items.append(('disk', d['dev'], d))
    for d in telemetry['standalone']:
        items.append(('disk', d['dev'], d))
    return items


def build_raid_menu_options(r, show_raw_detail=False):
    tel       = r['sync_tel']
    action    = tel['sync_action']
    is_paused = tel['is_paused']
    pct       = tel['pct']

    opts = []
    if action in ('check', 'repair', 'resync'):
        opts.append(('pause', f"⏸  Pause {action.upper()} (save progress at {pct:.1f}% / sector {tel['cur_sectors']:,})"))
    else:
        opts.append(('pause', f"{GRAY}⏸  Pause Check (currently idle){RESET}"))

    if is_paused:
        opts.append(('resume', f"▶  Resume Check from {pct:.1f}% (sector {tel['sync_min']:,})"))
    elif action == 'idle':
        opts.append(('resume', "▶  Start / Resume Check"))
    else:
        opts.append(('resume', f"{GRAY}▶  Resume Check (already running){RESET}"))

    opts.append(('full_check', "↻  Start Full Check from 0.0% (reset saved progress)"))

    if is_paused or action != 'idle':
        opts.append(('stop_reset', "⏹  Stop & Reset Check (set idle & clear saved sector)"))

    opts.append(('repair',     "🛠  Start Parity Repair Scrub (repair mismatches)"))
    opts.append(('spd_quiet',  "🔉 Speed Profile: Quiet / Background (max 50 MB/s)"))
    opts.append(('spd_normal', "⚡ Speed Profile: System Default (max 200 MB/s)"))
    opts.append(('spd_turbo',  "🚀 Speed Profile: Turbo / Unthrottled (max 1,000 MB/s)"))
    opts.append(('toggle_raw', f"📋 {'Hide' if show_raw_detail else 'Show'} Full mdadm --detail Output"))
    opts.append(('back',       "↩  Back to Main View (Esc)"))
    return opts


def render_raid_modal(r, menu_idx, show_raw_detail=False, confirm_full=False, status_msg=None):
    tel    = r['sync_tel']
    lines  = []
    md     = r['md']
    su     = r['su']
    st_col = r['state_col']

    lines.append(f'  {DARK_GRAY}╭─── {BLUE}{BOLD}RAID Array Control & Telemetry: {md}{RESET} {DARK_GRAY}' + '─' * max(10, 48 - len(md)) + f'{RESET}')
    lines.append(
        f'  {DARK_GRAY}│{RESET} {GRAY}State:{RESET}      {st_col}■ {BOLD}{su}{RESET} ({r["state_raw"]})    '
        f'{GRAY}Level:{RESET} {WHITE}{r["level"]}{RESET}    '
        f'{GRAY}Size:{RESET} {WHITE}{r["size"]}{RESET}    '
        f'{GRAY}Chunk:{RESET} {WHITE}{r["chunk_size"] or "N/A"}{RESET}'
    )
    lines.append(
        f'  {DARK_GRAY}│{RESET} {GRAY}Devices:{RESET}    {WHITE}{r["active"]}/{r["total"]} active{RESET} '
        f'(Working: {r["working"]}, Failed: {RED if r["failed"] != "0" else GREEN}{r["failed"]}{RESET}, Spare: {r["spare"]})    '
        f'{GRAY}Mismatches:{RESET} {RED if r["mismatch"] else GREEN}{r["mismatch"]}{RESET}'
    )
    lines.append(
        f'  {DARK_GRAY}│{RESET} {GRAY}UUID:{RESET}       {WHITE}{r["uuid"]}{RESET}    '
        f'{GRAY}Layout:{RESET} {WHITE}{r["layout"] or "N/A"}{RESET}    '
        f'{GRAY}Events:{RESET} {WHITE}{r["events"]}{RESET}'
    )

    # Sync / Scrub progress bar & sector offset details
    bar_col = CYAN if tel['sync_action'] == 'check' else (YELLOW if tel['is_paused'] else GREEN)
    spd_str = f"{tel['sync_speed_kb'] / 1024:.1f} MB/s" if tel['sync_speed_kb'] > 0 else "0.0 MB/s"
    eta_str = format_eta(tel['mdstat_finish']) if tel['mdstat_finish'] else (
        "Paused" if tel['is_paused'] else "Idle"
    )
    lines.append(f'  {DARK_GRAY}│{RESET}')
    lines.append(
        f'  {DARK_GRAY}│{RESET} {GRAY}Scrub/Sync:{RESET} {usage_bar(tel["pct"], 20, bar_col)} '
        f'{BOLD}{WHITE}{tel["pct"]:.2f}%{RESET}  '
        f'{GRAY}Action:{RESET} {CYAN}{tel["sync_action"]}{RESET}  '
        f'{GRAY}Speed:{RESET} {WHITE}{spd_str}{RESET}  '
        f'{GRAY}Status:{RESET} {YELLOW if tel["is_paused"] else WHITE}{eta_str}{RESET}'
    )
    lines.append(
        f'  {DARK_GRAY}│{RESET} {GRAY}Sectors:{RESET}    {WHITE}{tel["cur_sectors"]:,}{RESET} / {GRAY}{tel["total_sectors"]:,}{RESET} '
        f'({GRAY}Saved sync_min:{RESET} {YELLOW if tel["sync_min"] > 0 else GRAY}{tel["sync_min"]:,}{RESET})  '
        f'{GRAY}Limits:{RESET} {WHITE}{tel["speed_min_raw"]} .. {tel["speed_max_raw"]} KB/s{RESET}'
    )

    if status_msg:
        ok, msg = status_msg
        col = GREEN if ok else RED
        lines.append(f'  {DARK_GRAY}│{RESET}')
        lines.append(f'  {DARK_GRAY}│{RESET} {col}{BOLD}➔ {msg}{RESET}')

    if confirm_full:
        lines.append(f'  {DARK_GRAY}│{RESET}')
        lines.append(
            f'  {DARK_GRAY}│{RESET} {YELLOW}{BOLD}⚠ Confirm resetting scrub progress ({tel["pct"]:.1f}%) back to 0.0%? '
            f'Press [y] to confirm or [n/Esc] to cancel.{RESET}'
        )
    else:
        lines.append(f'  {DARK_GRAY}│{RESET}')
        lines.append(f'  {DARK_GRAY}│{RESET} {BOLD}{WHITE}Select Action (↑/↓ to move, Enter to run, Esc to close):{RESET}')
        opts = build_raid_menu_options(r, show_raw_detail)
        for idx, (_, label) in enumerate(opts):
            if idx == menu_idx:
                lines.append(f'  {DARK_GRAY}│{RESET}   {BG_SEL}{CYAN}{BOLD}❯ {label} {RESET}')
            else:
                lines.append(f'  {DARK_GRAY}│{RESET}     {WHITE}{label}{RESET}')

    if show_raw_detail:
        lines.append(f'  {DARK_GRAY}│{RESET}')
        lines.append(f'  {DARK_GRAY}│{RESET} {BOLD}{CYAN}─── Raw mdadm --detail {md} ───{RESET}')
        for raw_ln in r['md_info'].splitlines():
            if raw_ln.strip():
                lines.append(f'  {DARK_GRAY}│{RESET}   {GRAY}{raw_ln}{RESET}')

    lines.append(f'  {DARK_GRAY}╰' + '─' * 84 + f'{RESET}')
    return lines


def run_interactive(telemetry):
    expanded_disks  = set()   # set of '/dev/sdX'
    active_raid_md  = None    # '/dev/mdX' when RAID modal is open
    raid_menu_idx   = 0
    show_raw_detail = False
    confirm_full    = False
    status_msg      = None    # (bool, str)
    cursor_idx      = 0
    scroll_offset   = 0

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    def cleanup_term():
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass
        sys.stdout.write('\033[?25h\033[?1049l')
        sys.stdout.flush()

    def sig_handler(signum, frame):
        cleanup_term()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    try:
        sys.stdout.write('\033[?1049h\033[?25l')
        sys.stdout.flush()
        tty.setcbreak(fd)

        last_fast_refresh = time.time()

        while True:
            now = time.time()
            if now - last_fast_refresh >= 2.0:
                refresh_raid_fast(telemetry)
                last_fast_refresh = now

            items = build_selectable_items(telemetry)
            if not items:
                break
            cursor_idx = max(0, min(cursor_idx, len(items) - 1))
            sel_type, sel_id, sel_obj = items[cursor_idx]

            term_cols, term_rows = shutil.get_terminal_size((100, 35))
            lines = []
            selected_line_idx = 0

            # Title Bar
            lines.append(
                f'  {BOLD}{WHITE}RAID Health Monitor{RESET} {GRAY}v{VERSION}{RESET}    '
                f'{DARK_GRAY}│{RESET} {GRAY}↑/↓: Select   Enter: Actions / Details   '
                f'a: Expand All   r: Refresh   q: Quit{RESET}'
            )
            lines.append(f'  {DARK_GRAY}' + '─' * min(term_cols - 4, 96) + f'{RESET}')

            for r in telemetry['raids']:
                is_raid_sel = (sel_type == 'raid' and sel_id == r['md'] and active_raid_md is None)
                if is_raid_sel or active_raid_md == r['md']:
                    selected_line_idx = len(lines)

                lines.extend(format_raid_lines(r, selected=is_raid_sel))

                if active_raid_md == r['md']:
                    lines.extend(
                        render_raid_modal(
                            r,
                            raid_menu_idx,
                            show_raw_detail=show_raw_detail,
                            confirm_full=confirm_full,
                            status_msg=status_msg,
                        )
                    )

                if r['disks']:
                    lines.append(format_disk_header('  '))
                    for d in r['disks']:
                        is_d_sel = (sel_type == 'disk' and sel_id == d['dev'] and active_raid_md is None)
                        is_d_exp = (d['dev'] in expanded_disks)
                        if is_d_sel:
                            selected_line_idx = len(lines)
                        lines.append(format_disk_row(d, selected=is_d_sel, expanded=is_d_exp, interactive=True))
                        if is_d_exp:
                            lines.extend(format_disk_expanded(d))
                lines.append('')

            if telemetry['standalone']:
                lines.append(f'  {MAGENTA}■ Standalone Disks{RESET}')
                lines.append(format_disk_header('  '))
                for d in telemetry['standalone']:
                    is_d_sel = (sel_type == 'disk' and sel_id == d['dev'] and active_raid_md is None)
                    is_d_exp = (d['dev'] in expanded_disks)
                    if is_d_sel:
                        selected_line_idx = len(lines)
                    lines.append(format_disk_row(d, selected=is_d_sel, expanded=is_d_exp, interactive=True))
                    if is_d_exp:
                        lines.extend(format_disk_expanded(d))
                lines.append('')

            if status_msg and active_raid_md is None:
                ok, msg = status_msg
                lines.append(f'  {GREEN if ok else RED}{BOLD}➔ {msg}{RESET}')

            lines.append(
                f'  {GRAY}Last updated: {telemetry["updated_at"]} '
                f'(Live RAID scrub telemetry refreshes every 2s · Press R for full SMART rescan){RESET}'
            )

            # Viewport scrolling if content exceeds terminal height
            avail_rows = max(5, term_rows - 1)
            if selected_line_idx < scroll_offset + 2:
                scroll_offset = max(0, selected_line_idx - 2)
            elif selected_line_idx >= scroll_offset + avail_rows - 4:
                scroll_offset = max(0, selected_line_idx - avail_rows + 5)
            scroll_offset = max(0, min(scroll_offset, max(0, len(lines) - avail_rows)))

            visible = lines[scroll_offset:scroll_offset + avail_rows]

            buf = ['\033[H']
            for ln in visible:
                buf.append(f'{ln}\033[K\r\n')
            buf.append('\033[J')
            sys.stdout.write(''.join(buf))
            sys.stdout.flush()

            key = read_key(timeout=2.0)
            if key is None:
                continue

            if key == 'CTRL_C':
                break

            # Inside RAID Action Modal
            if active_raid_md is not None:
                r_obj = next((x for x in telemetry['raids'] if x['md'] == active_raid_md), None)
                if not r_obj:
                    active_raid_md = None
                    continue

                if confirm_full:
                    if key in ('y', 'Y'):
                        status_msg = action_start_full_check(active_raid_md)
                        confirm_full = False
                        refresh_raid_fast(telemetry)
                    elif key in ('n', 'N', 'ESC', 'BACKSPACE', 'q'):
                        confirm_full = False
                        status_msg = (False, f"{active_raid_md}: Full check reset cancelled.")
                    continue

                opts = build_raid_menu_options(r_obj, show_raw_detail)
                if key in ('UP', 'k'):
                    raid_menu_idx = (raid_menu_idx - 1) % len(opts)
                elif key in ('DOWN', 'j'):
                    raid_menu_idx = (raid_menu_idx + 1) % len(opts)
                elif key in ('ESC', 'BACKSPACE', 'q', 'LEFT'):
                    active_raid_md  = None
                    confirm_full    = False
                    show_raw_detail = False
                elif key in ('ENTER', 'SPACE'):
                    act_key = opts[raid_menu_idx][0]
                    if act_key == 'pause':
                        status_msg = action_pause_check(active_raid_md)
                        refresh_raid_fast(telemetry)
                    elif act_key == 'resume':
                        status_msg = action_resume_check(active_raid_md)
                        refresh_raid_fast(telemetry)
                    elif act_key == 'full_check':
                        tel_now = r_obj['sync_tel']
                        if tel_now['cur_sectors'] > 0 or tel_now['sync_min'] > 0:
                            confirm_full = True
                        else:
                            status_msg = action_start_full_check(active_raid_md)
                            refresh_raid_fast(telemetry)
                    elif act_key == 'stop_reset':
                        status_msg = action_stop_reset_check(active_raid_md)
                        refresh_raid_fast(telemetry)
                    elif act_key == 'repair':
                        status_msg = action_start_repair(active_raid_md)
                        refresh_raid_fast(telemetry)
                    elif act_key == 'spd_quiet':
                        status_msg = action_set_speed(active_raid_md, 1000, 50000, 'Quiet (50 MB/s max)')
                        refresh_raid_fast(telemetry)
                    elif act_key == 'spd_normal':
                        status_msg = action_set_speed(active_raid_md, 1000, 200000, 'System Default (200 MB/s max)')
                        refresh_raid_fast(telemetry)
                    elif act_key == 'spd_turbo':
                        status_msg = action_set_speed(active_raid_md, 200000, 1000000, 'Turbo (1,000 MB/s max)')
                        refresh_raid_fast(telemetry)
                    elif act_key == 'toggle_raw':
                        show_raw_detail = not show_raw_detail
                    elif act_key == 'back':
                        active_raid_md  = None
                        confirm_full    = False
                        show_raw_detail = False
                continue

            # Main View Key Handling
            if key in ('q', 'Q', 'ESC'):
                break
            elif key in ('UP', 'k'):
                cursor_idx = (cursor_idx - 1) % len(items)
            elif key in ('DOWN', 'j'):
                cursor_idx = (cursor_idx + 1) % len(items)
            elif key in ('ENTER', 'SPACE', 'RIGHT'):
                if sel_type == 'raid':
                    active_raid_md  = sel_id
                    raid_menu_idx   = 0
                    confirm_full    = False
                    status_msg      = None
                elif sel_type == 'disk':
                    if sel_id in expanded_disks:
                        expanded_disks.remove(sel_id)
                    else:
                        expanded_disks.add(sel_id)
            elif key == 'LEFT':
                if sel_type == 'disk' and sel_id in expanded_disks:
                    expanded_disks.remove(sel_id)
            elif key in ('a', 'A'):
                all_disk_ids = [i[1] for i in items if i[0] == 'disk']
                if len(expanded_disks) == len(all_disk_ids):
                    expanded_disks.clear()
                else:
                    expanded_disks = set(all_disk_ids)
            elif key == 'r':
                refresh_raid_fast(telemetry)
                status_msg = (True, "Refreshed live RAID telemetry.")
            elif key == 'R':
                sys.stdout.write(f'\033[H\n  {CYAN}{BOLD}Rescanning SMART attributes across all drives...{RESET}\033[J')
                sys.stdout.flush()
                telemetry  = collect_full_telemetry()
                status_msg = (True, "Completed full SMART & RAID hardware rescan.")

    finally:
        cleanup_term()

    # Leave the latest static summary on the terminal screen upon exiting
    render_static(telemetry)


################################################################################
# CLI & Main Entrypoint
################################################################################

def print_help():
    print(f"""{BOLD}RAID Health Monitor{RESET} (v{VERSION})

Usage:
  raid [options]                 Launch interactive arrow-key TUI (default in terminal)
  raid --static                  Print static summary and exit (for cron / MOTD scripts)

Options:
  -s, --static                   Static non-interactive output (auto-enabled when piped/cron)
  --pause [md]                   Pause active check/scrub and preserve progress in sync_min
  --resume [md]                  Resume check/scrub from saved sync_min sector offset
  --check-full [md]              Reset sync_min=0 and start full parity check from 0%
  --stop-check [md]              Stop check and reset sync_min=0
  -h, --help                     Show this help message and exit
  -v, --version                  Show version information and exit

Interactive Controls:
  ↑ / ↓ (or k / j)               Move selection between RAID arrays and physical drives
  Enter / Space                  Open RAID Action Menu (on RAID) or Expand SMART Details (on Drive)
  a                              Expand / collapse all drive details
  r / R                          Fast RAID refresh (r) or full SMART rescan (R)
  q / Esc                        Quit interactive view
""")


def resolve_target_raids(arg_flag):
    raids = get_raids()
    idx = sys.argv.index(arg_flag)
    if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith('-'):
        target = sys.argv[idx + 1]
        if not target.startswith('/dev/'):
            target = f'/dev/{target}'
        return [target]
    return raids


def main():
    if '--version' in sys.argv or '-v' in sys.argv:
        print(f"raid v{VERSION}")
        sys.exit(0)
    if '--help' in sys.argv or '-h' in sys.argv:
        print_help()
        sys.exit(0)

    check_privileges()
    check_deps()

    # Handle direct CLI RAID action flags if provided
    for flag, fn in (
        ('--pause',      action_pause_check),
        ('--resume',     action_resume_check),
        ('--check-full', action_start_full_check),
        ('--stop-check', action_stop_reset_check),
    ):
        if flag in sys.argv:
            targets = resolve_target_raids(flag)
            if not targets:
                print(f'{RED}No MDADM RAID arrays found.{RESET}', file=sys.stderr)
                sys.exit(1)
            for md in targets:
                ok, msg = fn(md)
                col = GREEN if ok else RED
                print(f'{col}■ {msg}{RESET}')
            sys.exit(0)

    static_mode = (
        '--static' in sys.argv
        or '-s' in sys.argv
        or not sys.stdin.isatty()
        or not sys.stdout.isatty()
    )

    if not static_mode:
        sys.stdout.write(f'\n  {CYAN}Scanning RAID arrays and drive SMART telemetry...{RESET}\r')
        sys.stdout.flush()

    telemetry = collect_full_telemetry()

    if static_mode:
        render_static(telemetry)
    else:
        run_interactive(telemetry)


if __name__ == '__main__':
    main()
