#!/usr/bin/env python3

################################################################################
# RAID Health Monitor
# Version: 15.0.0 - Python
################################################################################

VERSION = "15.0.0"

import os
import re
import sys
import shutil
import subprocess
from datetime import datetime

################################################################################
# Colors
################################################################################

RED     = '\033[38;5;203m'
GREEN   = '\033[38;5;150m'
YELLOW  = '\033[38;5;221m'
BLUE    = '\033[38;5;111m'
CYAN    = '\033[38;5;117m'
MAGENTA = '\033[38;5;183m'
GRAY    = '\033[38;5;245m'
WHITE   = '\033[38;5;255m'
BOLD    = '\033[1m'
RESET   = '\033[0m'

################################################################################
# Helpers
################################################################################

def run(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return r.stdout
    except Exception:
        return ''


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


def usage_bar(pct, width=10):
    fill  = int(pct * width / 100)
    empty = width - fill
    col   = RED if pct > 80 else YELLOW if pct > 60 else GREEN
    return f'{col}{"▰" * fill}{GRAY}{"▱" * empty}{RESET}'


def check_deps():
    for cmd in ('mdadm', 'parted', 'smartctl', 'lsblk'):
        if not shutil.which(cmd):
            print(f'ERROR: Missing {cmd}', file=sys.stderr)
            sys.exit(1)
    if os.geteuid() != 0:
        print(f'\n{RED}ERROR: Root privileges required.{RESET}', file=sys.stderr)
        print(f'Re-run with: {WHITE}sudo {sys.argv[0]}{RESET}\n', file=sys.stderr)
        sys.exit(1)


################################################################################
# Info Gathering
################################################################################

def format_eta(min_str):
    try:
        minutes = float(min_str.replace('min', ''))
        days = int(minutes // 1440)
        hours = int((minutes % 1440) // 60)
        
        from datetime import datetime, timedelta
        eta_date = datetime.now() + timedelta(minutes=minutes)
        eta_str = eta_date.strftime("%b %d, %H:%M")
        
        if days > 0:
            return f"{days}d {hours}h (ETA: {eta_str})"
        return f"{hours}h (ETA: {eta_str})"
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


def get_disks_from_parted(parted_out, filter_devs):
    """
    Parse parted -l output into list of dicts: {model, dev, size, type}.
    Matches by base device name (trailing digits stripped) so /dev/sdb1 -> /dev/sdb.
    """
    disks      = []
    model      = None
    # Strip trailing digits so /dev/sdb1 matches parted's /dev/sdb
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
    info = {'temp': 0, 'uncorr': 0, 'pend': 0, 'realloc': 0, 'poh': 0, 'serial': 'UNK'}

    for line in out.splitlines():
        f = line.split()
        def col(n):
            try: return f[n]
            except IndexError: return ''

        if re.search(r'Temperature_Celsius',     line, re.I) and not info['temp']:
            try: info['temp']    = int(col(9))
            except ValueError: pass
        elif re.search(r'Offline_Uncorrectable',  line, re.I) and not info['uncorr']:
            try: info['uncorr']  = int(col(9))
            except ValueError: pass
        elif re.search(r'Current_Pending_Sector', line, re.I) and not info['pend']:
            try: info['pend']    = int(col(9))
            except ValueError: pass
        elif re.search(r'Reallocated_Sector_Ct',  line, re.I) and not info['realloc']:
            try: info['realloc'] = int(col(9))
            except ValueError: pass
        elif re.search(r'Power_On_Hours|Power On Hours', line, re.I) and not info['poh']:
            try: info['poh']     = int(f[-1].replace(',', ''))
            except ValueError: pass
        elif line.startswith('Serial Number:') and info['serial'] == 'UNK':
            try: info['serial']  = f[-1]
            except IndexError: pass

    return info


def get_member_states(md_info):
    """Return dict of {'/dev/sdX': 'active sync'} from mdadm --detail output.
    Keyed by both raw path and base path (digits stripped) for safe lookup."""
    states = {}
    for line in md_info.splitlines():
        m = re.search(r'((?:active|spare|faulty|rebuild|resync)[^/]*)(/dev/(?!md)\S+)', line)
        if m:
            state = m.group(1).strip()
            dev   = m.group(2).strip()
            states[dev] = state
            states[re.sub(r'\d+$', '', dev)] = state  # also index by base
    return states


def filter_devs(all_devs, remove):
    remove_bases = {re.sub(r'\d+$', '', d) for d in remove}
    return [d for d in all_devs if re.sub(r'\d+$', '', d) not in remove_bases]


################################################################################
# Display: RAID arrays
################################################################################

def show_raid(md, md_info):

    def field(label):
        for line in md_info.splitlines():
            if label in line and ':' in line:
                return line.split(':', 1)[1].strip()
        return ''

    level  = field('Raid Level')
    total  = field('Total Devices')
    active = field('Active Devices')
    state  = field('State :')

    m    = re.search(r'Array Size\s*:\s*(\d+)', md_info)
    size = kb_to_human(m.group(1)) if m else '?'

    mismatch = 0
    try:
        mismatch = int(open(f'/sys/block/{os.path.basename(md)}/md/mismatch_cnt').read())
    except (OSError, ValueError):
        pass

    # Mount point + usage bar
    mount_str = ''
    usage_str = ''
    for line in run('df -hP 2>/dev/null').splitlines():
        if md in line:
            parts = line.split()
            if len(parts) >= 6:
                mount_str = f' {GRAY}({parts[5]}){RESET}'
                pct_s = parts[4].rstrip('%')
                if pct_s.isdigit():
                    pct       = int(pct_s)
                    usage_str = f'{usage_bar(pct)} {pct}% {GRAY}({parts[2]}/{parts[1]}){RESET}'
            break

    # State color + label
    su = state.upper()
    if   'ACTIVE'   in su: state_col, su = GREEN,  'ACTIVE'
    elif 'CLEAN'    in su: state_col, su = CYAN,   'CLEAN'
    elif 'DEGRADED' in su: state_col, su = RED,    'DEGRADED'
    else:                  state_col      = GRAY

    # Sync / rebuild progress
    sync_str = ''
    md_short = os.path.basename(md)
    try:
        mdstat_lines = open('/proc/mdstat').read().splitlines()
        for i, ln in enumerate(mdstat_lines):
            if ln.startswith(md_short + ' '):
                block = '\n'.join(mdstat_lines[i:i+3])
                sm    = re.search(r'(recovery|resync|check|reshape).*?=\s*(\S+).*?finish=(\S+)', block)
                if sm:
                    op = sm.group(1)
                    if op == 'check':
                        state_col, su = CYAN, 'CHECKING'
                        sync_str = f'    {GRAY}Scrub:{RESET} {CYAN}{sm.group(2)} {format_eta(sm.group(3))}{RESET}'
                    else:
                        state_col, su = YELLOW, 'REBUILDING'
                        sync_str = f'    {GRAY}Sync:{RESET} {YELLOW}{sm.group(2)} {format_eta(sm.group(3))}{RESET}'
                        break
    except OSError:
        pass

    # Anomaly check
    anomaly = (
        mismatch != 0
        or su in ('DEGRADED', 'REBUILDING')
        or (active.isdigit() and total.isdigit() and active != total)
    )

    if not anomaly:
        # All on one line when healthy
        line1 = f'{state_col}■ {su}{RESET}  {BLUE}{md}{RESET}  {GRAY}{level}{RESET}    {GRAY}{active}/{total} devices{RESET}'
        if usage_str:
            line1 += f'    {GRAY}Usage:{RESET} {usage_str}'
        if mount_str:
            line1 += f'    {mount_str}'
        print(line1)
    else:
        # Identity line, then detail on second line
        line1 = f'{state_col}■ {su}{RESET}  {BLUE}{md}{RESET}  {GRAY}{level}{RESET}'
        if mount_str:
            line1 += f'    {mount_str}'
        print(line1)
        detail = (f'  {YELLOW}!{RESET}    '
                  f'{GRAY}Mismatch: {RED if mismatch else GREEN}{mismatch}{RESET}    '
                  f'{GRAY}Active: {WHITE}{active}/{total}{RESET}')
        if usage_str:
            detail += f'    {GRAY}Usage:{RESET} {usage_str}'
        if sync_str:
            detail += sync_str
        print(detail)


################################################################################
# Display: Disk table
################################################################################

def show_disks(devs, parted_out, md_info=None):
    disk_list     = get_disks_from_parted(parted_out, devs)
    member_states = get_member_states(md_info) if md_info else {}

    print(f'  {BOLD}{WHITE}{"Model":<24} {"Device":<10} {"Serial":<10} {"Size":<8} '
          f'{"Type":<6} {"POH":<6} {"Temp":<8} {"Errors":<18} State{RESET}')

    for d in disk_list:
        smart = get_smart_info(d['dev'])

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

        state_txt = member_states.get(d['dev'], '')
        if   re.search(r'faulty|fail',           state_txt): state_col = RED
        elif re.search(r'spare',                  state_txt): state_col = BLUE
        elif re.search(r'rebuild|recover|resync', state_txt): state_col = YELLOW
        else:                                                  state_col = GRAY

        model_col = RED if RED in (poh_col, temp_col, err_col, state_col) else WHITE

        print(f'  {model_col}{model:<24}{RESET} '
              f'{CYAN}{d["dev"]:<10}{RESET} '
              f'{GRAY}{serial_fmt:<10}{RESET} '
              f'{WHITE}{d["size"]:<8}{RESET} '
              f'{type_col}{d["type"]:<6}{RESET} '
              f'{poh_col}{poh_fmt:<6}{RESET} '
              f'{temp_col}{temp_out:<8}{RESET} '
              f'{err_col}{err_str:<18}{RESET} '
              f'{state_col}{state_txt}{RESET}')



################################################################################
# Main
################################################################################

def print_help():
    print(f"""{BOLD}RAID Health Monitor{RESET} (v{VERSION})

Usage:
  sudo {sys.argv[0]} [options]

Options:
  -h, --help     Show this help message and exit
  -v, --version  Show version information and exit

Description:
  Monitors Linux MDADM software RAID arrays and physical disk SMART attributes.
  Requires root privileges to query low-level SMART and parted diagnostics.
""")


def main():
    if '--version' in sys.argv or '-v' in sys.argv:
        print(f"raid v{VERSION}")
        sys.exit(0)
    if '--help' in sys.argv or '-h' in sys.argv:
        print_help()
        sys.exit(0)

    check_deps()
    print()

    parted_out = run('parted -l 2>&1')
    all_devs   = get_all_devs(parted_out)
    raids      = get_raids()
    non_raid   = list(all_devs)

    for md in raids:
        md_info = run(f'mdadm --detail {md} 2>/dev/null')
        if not md_info.strip():
            continue

        show_raid(md, md_info)

        members  = re.findall(r'/dev/(?!md)\S+', md_info)
        non_raid = filter_devs(non_raid, members)

        if members:
            show_disks(members, parted_out, md_info)

        print()

    if non_raid:
        print(f'{MAGENTA}■ Standalone Disks{RESET}')
        show_disks(non_raid, parted_out)
        print()


    print(f'{GRAY}Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}{RESET}\n')


if __name__ == '__main__':
    main()

