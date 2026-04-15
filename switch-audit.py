#!/usr/bin/env python3
import re
import time
import getpass
import urllib.request
import urllib.error
from netmiko import ConnectHandler

def get_vendor(mac):
    try:
        url = f"https://api.macvendors.com/{mac}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req)
        vendor = response.read().decode('utf-8')
        time.sleep(1)  # Respect free API rate limits (1 req/sec)
        return vendor
    except Exception:
        time.sleep(1)
        return "Unknown Vendor"

def get_device_type(ip, mac="", vlan=""):
    if mac.startswith("00:05:1B") or mac.startswith("00:05:50"):
        return "RDM/DCI"

    if not ip or ip == "Unknown":
        return "Unknown"
    
    if ip in ['192.168.100.2', '192.168.100.3']:
        return "CCTV"
        
    try:
        parts = ip.split('.')
        if len(parts) == 4 and parts[3].isdigit():
            last = int(parts[3])
            if 1 <= last <= 9 and str(vlan) == "1": return "Till"
            elif 10 <= last <= 19: return "Volumatics"
            elif last == 20: return "Bakery"
            elif last == 50: return "RAP"
            elif 51 <= last <= 54: return "Self Checkout"
            elif last == 55: return "Alarm"
            elif last == 101: return "Back Office PC"
            elif last == 150: return "Printer"
            elif 231 <= last <= 239: return "Wireless AP for labels"
    except Exception:
        pass
        
    return "Other"

def main():
    print("=== Network Device Audit ===")
    
    sites = [
        {"name": "0320 - EAST PRESTON (15/04)", "switch_ip": "172.31.90.43", "router_ip": "172.31.90.44"},
        {"name": "0220 - ANGMERING (15/04)", "switch_ip": "172.31.84.59", "router_ip": "172.31.84.60"},
        {"name": "0326 - CHOBHAM (16/04)", "switch_ip": "172.31.90.155", "router_ip": "172.31.90.156"},
        {"name": "0023 - NORTH BADDESLEY (16/04)", "switch_ip": "172.31.89.11", "router_ip": "172.31.89.12"},
        {"name": "9719 - ADDLESTONE (16/04)", "switch_ip": "172.31.96.219", "router_ip": "172.31.96.220"},
        {"name": "0406 - WEST END (16/04)", "switch_ip": "172.31.94.11", "router_ip": "172.31.94.12"},
    ]

    username = input("Enter Username: ")
    password = getpass.getpass("Enter Password: ")

    for site in sites:
        print(f"\n{'='*115}")
        print(f"=== Auditing Site: {site['name']} ===")
        print(f"=== Router IP: {site['router_ip']} | Switch IP: {site['switch_ip']} ===")
        print(f"{'='*115}")

        router_device = {
            'device_type': 'cisco_xe',
            'host': site['router_ip'],
            'username': username,
            'password': password,
            'global_delay_factor': 2
        }

        switch_device = {
            'device_type': 'aruba_osswitch',
            'host': site['switch_ip'],
            'username': username,
            'password': password,
            'global_delay_factor': 2
        }

        arp_table = {}
        
        print("\n[+] Connecting to Cisco Router...")
        try:
            with ConnectHandler(**router_device) as router_ssh:
                print("[+] Connected to router. Retrieving ARP table...")
                arp_output = router_ssh.send_command("show ip arp")
                
                # Typical IOS-XE ARP output: Protocol  Address  Age (min)  Hardware Addr   Type   Interface
                for line in arp_output.splitlines():
                    # Extract IP
                    ip_match = re.search(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', line)
                    if not ip_match:
                        continue
                    ip_addr = ip_match.group(0)
                    
                    # Extract MAC
                    mac_match = re.search(r'([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})', line)
                    if mac_match:
                        mac_raw = mac_match.group(1).replace('.', '')
                        mac_norm = ':'.join(mac_raw[i:i+2] for i in range(0, 12, 2)).upper()
                        arp_table[mac_norm] = ip_addr
        except Exception as e:
            print(f"[-] Error querying router: {e}")
            continue

        mac_port_map = []
        
        print("\n[+] Connecting to Aruba Switch...")
        try:
            with ConnectHandler(**switch_device) as switch_ssh:
                print("[+] Connected to switch. Retrieving MAC addresses on VLAN 1 and VLAN 30...")
                # Often it's 'show mac-address vlan 1' on Aruba OS switches
                vlan_outputs = {
                    "1": switch_ssh.send_command("show mac-address vlan 1"),
                    "30": switch_ssh.send_command("show mac-address vlan 30")
                }
                
                for vlan_id, mac_output in vlan_outputs.items():
                    lines = mac_output.splitlines()
                    for line in lines:
                        parts = line.split()
                        if len(parts) >= 2:
                            mac_candidate = parts[0]
                            # Check if the first column is a MAC address (e.g., 001122-334455 or 00:11:22:..)
                            clean_mac = re.sub(r'[^0-9a-fA-F]', '', mac_candidate)
                            
                            if len(clean_mac) == 12:
                                # Aruba formatting can vary between models (OS vs CX)
                                # e.g., "00:11:22:33:44:55    1    dynamic    1/1/1" (CX)
                                # or    "001122-334455 | 1" or "001122-334455  1" (OS)
                                if 'dynamic' in [p.lower() for p in parts] or 'static' in [p.lower() for p in parts]:
                                    port_candidate = parts[-1]
                                elif len(parts) >= 3 and parts[1] == '|':
                                    port_candidate = parts[2]
                                else:
                                    port_candidate = parts[-1] if len(parts) > 1 else "Unknown"
                                    
                                # For display and matching, normalize to XX:XX:XX:XX:XX:XX
                                mac_norm = ':'.join(clean_mac[i:i+2] for i in range(0, 12, 2)).upper()
                                mac_port_map.append({'port': port_candidate, 'mac': mac_norm, 'vlan': vlan_id})
        except Exception as e:
            print(f"[-] Error querying switch: {e}")
            continue

        print(f"\n[+] Found {len(mac_port_map)} devices across VLAN 1 & 30. Resolving Vendors (takes ~1s per MAC)...")
        
        table_data = []
        
        for entry in mac_port_map:
            mac = entry['mac']
            port = entry['port']
            vlan = entry.get('vlan', '')
            ip = arp_table.get(mac, "Unknown")
            
            if ip == "192.168.100.1":
                continue
                
            vendor = get_vendor(mac)
            description = get_device_type(ip, mac, vlan)
            
            table_data.append({
                'port': port,
                'mac': mac,
                'ip': ip,
                'vendor': vendor,
                'description': description
            })

        # Sort the table data naturally by port number (e.g., 1, 2, 10, 1/1/1, 1/1/2)
        table_data.sort(key=lambda x: [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', str(x['port']))])

        print("-" * 115)
        print(f"{'PORT':<10} | {'MAC ADDRESS':<20} | {'IP ADDRESS':<18} | {'VENDOR':<25} | {'DESCRIPTION':<30}")
        print("-" * 115)
        
        for r in table_data:
            print(f"{r['port']:<10} | {r['mac']:<20} | {r['ip']:<18} | {r['vendor'][:23]:<25} | {r['description']:<30}")

        print("-" * 115)
        print(f"\n[+] Audit completed for {site['name']}.")

    print("\n[+] All site audits completed. SSH sessions have been closed.")

if __name__ == '__main__':
    main()

