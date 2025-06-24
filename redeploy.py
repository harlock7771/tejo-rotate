import boto3
import time
import json
import random
import string
import os
import subprocess
import configparser
import concurrent.futures
import argparse # Import modul argparse untuk menangani argumen
from botocore.exceptions import ClientError

# --- KONFIGURASI UTAMA ---
# Edit semua pengaturan di bagian ini sesuai kebutuhan Anda.

# 1. LOKASI FILE AKUN AWS
AWS_ACCOUNTS_FILE = "/opt/cloud-iprotate/aws_accounts.json"
# Pastikan jalur ini sesuai dengan lokasi file aws_accounts.json Anda.

# 2. KONFIGURASI DEPLOYMENT
AWS_REGION = "us-east-1"
INSTANCE_TYPE = "t2.micro"
CONFIG_PATH = "/opt/cloud-iprotate/config.conf"
# Path ke file config.conf yang digunakan router Anda

# 3. PERINTAH INSTALASI
INSTALL_COMMAND = "sleep 10 && sudo curl -sSL https://raw.githubusercontent.com/harlock7771/cloud-iprotate/main/install-service.sh | sudo bash -s"

# --- FUNGSI BANTUAN (DIADAPTASI DARI MAIN.PY & HEALTH_MONITOR.PY) ---

def load_aws_accounts():
    """Memuat daftar akun AWS dari file JSON."""
    if not os.path.exists(AWS_ACCOUNTS_FILE):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: File '{AWS_ACCOUNTS_FILE}' tidak ditemukan.")
        return []
    try:
        with open(AWS_ACCOUNTS_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Gagal membaca JSON dari '{AWS_ACCOUNTS_FILE}': {e}")
        return []

def save_aws_accounts(accounts):
    """Menyimpan daftar akun AWS ke file JSON."""
    try:
        with open(AWS_ACCOUNTS_FILE, 'w') as f:
            json.dump(accounts, f, indent=4)
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Gagal menyimpan akun ke '{AWS_ACCOUNTS_FILE}': {e}")

def create_boto_client(service_name, account_creds):
    """Membuat klien boto3 untuk akun tertentu."""
    return boto3.client(
        service_name,
        aws_access_key_id=account_creds["accessKey"],
        aws_secret_access_key=account_creds["secretKey"],
        region_name=AWS_REGION
    )

def cleanup_existing_instances(ec2_client, thread_id="main"):
    """Mencari dan menghapus semua instance yang ada di akun BARU sebelum mulai."""
    print(f"\n>>> [Thread: {thread_id}] Memeriksa instance yang sudah ada...")
    try:
        response = ec2_client.describe_instances(
            Filters=[{'Name': 'instance-state-name', 'Values': ['pending', 'running', 'shutting-down', 'stopping', 'stopped']}]
        )
        instance_ids_to_terminate = [inst['InstanceId'] for res in response['Reservations'] for inst in res['Instances']]
        if not instance_ids_to_terminate:
            print(f"  - [Thread: {thread_id}] Akun bersih. Tidak ada yang perlu dihapus.")
            return True
        print(f"  - [Thread: {thread_id}] Ditemukan {len(instance_ids_to_terminate)} instance di akun ini yang akan dihapus: {instance_ids_to_terminate}")
        ec2_client.terminate_instances(InstanceIds=instance_ids_to_terminate)
        waiter = ec2_client.get_waiter('instance_terminated')
        waiter.wait(InstanceIds=instance_ids_to_terminate)
        print(f"  - [Thread: {thread_id}] Pembersihan akun selesai.")
        return True
    except ClientError as e:
        print(f"  - [Thread: {thread_id}] PERINGATAN: Gagal membersihkan instance di akun. Error: {e}")
        return False

def get_latest_amazon_linux_ami(ec2_client):
    """Mencari AMI Amazon Linux 2023 terbaru."""
    print("  - Mencari AMI terbaru...")
    response = ec2_client.describe_images(Owners=['amazon'], Filters=[
        {'Name': 'name', 'Values': ['al2023-ami-2023.*-kernel-6.1-x86_64']},
        {'Name': 'state', 'Values': ['available']}, {'Name': 'architecture', 'Values': ['x86_64']}
    ])
    images = sorted(response['Images'], key=lambda x: x['CreationDate'], reverse=True)
    if not images: raise Exception("Tidak ada AMI Amazon Linux 2023 yang ditemukan.")
    ami_id = images[0]['ImageId']
    print(f"    - AMI ditemukan: {ami_id}")
    return ami_id

def create_ssm_role_and_profile(iam_client, role_name, profile_name, thread_id="main"):
    """Membuat IAM Role dan Instance Profile untuk SSM."""
    print(f"  - [Thread: {thread_id}] Memeriksa atau membuat IAM Role: {role_name}")
    trust_policy = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    try:
        iam_client.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust_policy)
        time.sleep(10)
    except ClientError as e:
        if e.response['Error']['Code'] != 'EntityAlreadyExists': raise e
    iam_client.attach_role_policy(RoleName=role_name, PolicyArn='arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore')
    
    print(f"  - [Thread: {thread_id}] Memeriksa atau membuat Instance Profile: {profile_name}")
    try:
        iam_client.create_instance_profile(InstanceProfileName=profile_name)
    except ClientError as e:
        if e.response['Error']['Code'] != 'EntityAlreadyExists': raise e
    try:
        iam_client.add_role_to_instance_profile(InstanceProfileName=profile_name, RoleName=role_name)
    except ClientError as e:
        if e.response['Error']['Code'] != 'LimitExceeded': raise e
    time.sleep(15)

def create_and_configure_security_group(ec2_client, sg_name, thread_id="main"):
    """Membuat Security Group yang mengizinkan semua lalu lintas."""
    print(f"  - [Thread: {thread_id}] Memeriksa atau membuat Security Group: {sg_name}")
    try:
        vpc_id = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])['Vpcs'][0]['VpcId']
        response = ec2_client.describe_security_groups(Filters=[{'Name': 'group-name', 'Values': [sg_name]}])
        if response['SecurityGroups']: return response['SecurityGroups'][0]['GroupId']
        sg_id = ec2_client.create_security_group(GroupName=sg_name, Description='Allow all traffic for deployment', VpcId=vpc_id)['GroupId']
        ec2_client.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{'IpProtocol': '-1', 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}])
        return sg_id
    except ClientError as e:
        raise e

def create_one_instance(ec2_client, ami_id, profile_name, sg_id, thread_id="main"):
    """Membuat SATU instance EC2 baru dengan IAM Profile dan SG yang benar."""
    print(f"    - [Thread: {thread_id}] Membuat 1 instance EC2 baru...")
    try:
        response = ec2_client.run_instances(
            ImageId=ami_id,
            InstanceType=INSTANCE_TYPE,
            MinCount=1,
            MaxCount=1,
            IamInstanceProfile={'Name': profile_name},
            SecurityGroupIds=[sg_id]
        )
        instance_id = response['Instances'][0]['InstanceId']
        print(f"    - [Thread: {thread_id}] Permintaan instance baru berhasil: {instance_id}")
        waiter = ec2_client.get_waiter('instance_running')
        waiter.wait(InstanceIds=[instance_id])
        print(f"    - [Thread: {thread_id}] Instance {instance_id} sudah 'running'.")
        return instance_id
    except ClientError as e:
        print(f"    - [Thread: {thread_id}] GAGAL membuat instance baru: {e}")
        return None

def wait_for_ssm_registration(ssm_client, instance_id, thread_id="main"):
    """Menunggu instance terdaftar dan siap di SSM."""
    print(f"    - [Thread: {thread_id}] Menunggu instance {instance_id} siap di SSM (bisa beberapa menit)...")
    timeout = time.time() + 60 * 5  # Timeout 5 menit
    while time.time() < timeout:
        try:
            response = ssm_client.describe_instance_information(
                InstanceInformationFilterList=[{'key': 'InstanceIds', 'valueSet': [instance_id]}]
            )
            # Pastikan daftar tidak kosong dan PingStatus adalah Online
            if response.get('InstanceInformationList') and response['InstanceInformationList'][0].get('PingStatus') == 'Online':
                print(f"    - [Thread: {thread_id}] Instance {instance_id} kini 'Online' di SSM.")
                return True
        except ClientError as e:
            # Mengabaikan error throttling yang mungkin terjadi saat polling
            if e.response.get('Error', {}).get('Code') == 'ThrottlingException':
                print(f"    - [Thread: {thread_id}] Terjadi throttling saat polling SSM, mencoba lagi...")
            else:
                print(f"    - [Thread: {thread_id}] Terjadi error saat polling SSM, mencoba lagi... ({e})")
        
        time.sleep(20)  # Tunggu 20 detik sebelum mencoba lagi

    print(f"!!! [Thread: {thread_id}] TIMEOUT: Instance {instance_id} tidak menjadi Online di SSM dalam 5 menit.")
    return False

def run_install_on_instance(ssm_client, instance_id, thread_id="main"):
    """Menjalankan perintah instalasi pada instance baru via SSM."""
    # Memanggil fungsi penunggu yang baru
    if not wait_for_ssm_registration(ssm_client, instance_id, thread_id):
        return False
        
    try:
        print(f"    - [Thread: {thread_id}] Menjalankan perintah instalasi pada {instance_id}...")
        response = ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName='AWS-RunShellScript',
            Parameters={'commands': [INSTALL_COMMAND]}, TimeoutSeconds=600
        )
        command_id = response['Command']['CommandId']
        
        # Polling manual untuk command_executed karena waiter terkadang kurang detail
        print(f"    - [Thread: {thread_id}] Menunggu perintah SSM '{command_id}' selesai...")
        timeout = time.time() + 60 * 10 # 10 menit timeout
        while time.time() < timeout:
            output = ssm_client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            status = output['Status']
            if status in ['Success', 'Failed', 'Cancelled', 'TimedOut']:
                if status == 'Success':
                    print(f"    - [Thread: {thread_id}] Perintah instalasi pada {instance_id} selesai dengan sukses.")
                    return True
                else:
                    print(f"    - [Thread: {thread_id}] GAGAL menjalankan perintah instalasi pada {instance_id}. Status: {status}")
                    print(f"      - Output: {output.get('StandardOutputContent', 'N/A')}")
                    print(f"      - Error: {output.get('StandardErrorContent', 'N/A')}")
                    return False
            time.sleep(15) # Polling setiap 15 detik
        
        print(f"!!! [Thread: {thread_id}] TIMEOUT: Perintah SSM pada {instance_id} tidak selesai dalam 10 menit.")
        return False
            
    except ClientError as e:
        print(f"    - [Thread: {thread_id}] GAGAL mengirim perintah instalasi ke {instance_id}: {e}")
        return False

def replace_one_section(section_name, old_instance_id, new_account_creds, new_ec2_client, new_iam_client, new_ssm_client, ami_id):
    """
    Fungsi worker untuk mengganti satu seksi. Dijalankan di dalam thread.
    Mengembalikan instance ID baru jika berhasil, None jika gagal.
    """
    thread_id = f"Section-{section_name}"
    print(f"\n>>> [Thread: {thread_id}] Memulai penggantian untuk seksi [{section_name}]...")
    
    run_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    role_name = f"ssm-role-replace-{run_id}"
    profile_name = f"ssm-profile-replace-{run_id}"
    sg_name = f"deploy-sg-replace-{run_id}"

    try:
        create_ssm_role_and_profile(new_iam_client, role_name, profile_name, thread_id)
        sg_id = create_and_configure_security_group(new_ec2_client, sg_name, thread_id)
    except Exception as e:
        print(f"!!! [Thread: {thread_id}] Gagal membuat prasyarat (IAM/SG) untuk seksi [{section_name}]. Error: {e}")
        return None

    new_instance_id = create_one_instance(new_ec2_client, ami_id, profile_name, sg_id, thread_id)
    if not new_instance_id:
        print(f"!!! [Thread: {thread_id}] Gagal membuat instance baru untuk seksi [{section_name}]. Melewatkan.")
        return None
        
    if not run_install_on_instance(new_ssm_client, new_instance_id, thread_id):
        print(f"!!! [Thread: {thread_id}] Gagal menjalankan instalasi pada instance baru {new_instance_id}. Melewatkan.")
        try:
            new_ec2_client.terminate_instances(InstanceIds=[new_instance_id])
            print(f"    - [Thread: {thread_id}] Instance {new_instance_id} yang gagal di-setup telah dihentikan.")
        except ClientError as e:
            print(f"    - [Thread: {thread_id}] PERINGATAN: Gagal menghentikan instance {new_instance_id} yang gagal. Error: {e}")
        return None

    print(f">>> [Thread: {thread_id}] Penggantian untuk seksi [{section_name}] berhasil. Instance baru: {new_instance_id}")
    return {"section_name": section_name, "old_instance_id": old_instance_id, "new_instance_id": new_instance_id}

def setup_router_server_redeploy(all_instances_data_for_update, config_file_path, new_account_creds): # Parameter diubah
    """
    Memperbarui file konfigurasi dan me-restart layanan router setelah redeploy.
    Menerima all_instances_data_for_update (list of dicts) dan new_account_creds.
    """
    print("\n>>> Mengonfigurasi VPS Server Lokal (dari redeploy.py) <<<")
    config = configparser.ConfigParser()
    config.optionxform = str # Pertahankan kapitalisasi nama section
    
    # Baca konfigurasi yang sudah ada
    if os.path.exists(config_file_path):
        with open(config_file_path, 'r') as f:
            config.read_string(f.read())
    else:
        print(f"!!! ERROR: File konfigurasi '{config_file_path}' tidak ditemukan. Tidak dapat memperbarui.")
        return False # Gagal memperbarui config

    for data in all_instances_data_for_update: 
        section_name = data['section_name'] # Nama section (port) dari config.conf lama
        
        # Perbarui konfigurasi dalam memori untuk section yang relevan
        if not config.has_section(section_name):
            # Ini seharusnya tidak terjadi jika redeploy berjalan dengan benar,
            # karena ini harusnya memperbarui section yang sudah ada.
            print(f"!!! PERINGATAN: Seksi [{section_name}] tidak ditemukan di config.conf saat update. Membuat baru.")
            config.add_section(section_name)

        config.set(section_name, 'type', 'aws')
        config.set(section_name, 'socks5Port', str(section_name)) # socks5Port sama dengan nama section
        config.set(section_name, 'httpPort', str(int(section_name) + 1000)) # Disesuaikan dengan format yang Anda inginkan
        config.set(section_name, 'accessKey', new_account_creds['accessKey']) # Gunakan kredensial akun baru
        config.set(section_name, 'secretKey', new_account_creds['secretKey']) # Gunakan kredensial akun baru
        config.set(section_name, 'instanceId', data['new_instance_id']) # Gunakan instance ID baru
        config.set(section_name, 'region', AWS_REGION) # Region tetap sama

    # Tidak perlu sentuh bagian [api] karena redeploy hanya memperbarui instance.
    # Bagian [api] sudah diinisialisasi oleh main.py.

    temp_path = "/tmp/config.conf.tmp"
    try:
        with open(temp_path, 'w') as f:
            config.write(f)
        subprocess.run(f"sudo mkdir -p {os.path.dirname(config_file_path)} && sudo mv {temp_path} {config_file_path}", shell=True, check=True)
        print("  - File konfigurasi berhasil disimpan.")
        
        subprocess.run("sudo pm2 restart index.js --name iprotate", shell=True, check=True, capture_output=True, text=True)
        print("  - Layanan router berhasil di-restart.")
        return True
    except Exception as e:
        print(f"!!! ERROR: Gagal menyimpan config.conf atau restart layanan dari redeploy.py: {e}")
        return False

def main():
    """Fungsi utama untuk menjalankan proses penggantian."""
    
    # 1. Parsing argumen baris perintah
    parser = argparse.ArgumentParser(description="Replace instances from an old AWS account with a new one.")
    parser.add_argument("--old-access-key", required=True, help="Access Key of the old (suspended) AWS account.")
    parser.add_argument("--new-account-id", required=True, help="ID of the new backup AWS account to use.")
    args = parser.parse_args()

    OLD_ACCESS_KEY = args.old_access_key
    NEW_ACCOUNT_ID = args.new_account_id

    print(f"--- Memulai Skrip Penggantian Akun ---")
    print(f"Mengganti instance dari Old Access Key: {OLD_ACCESS_KEY}")
    print(f"Menggunakan Akun Baru (ID): {NEW_ACCOUNT_ID}")

    # 2. Muat semua akun AWS dari aws_accounts.json
    all_aws_accounts = load_aws_accounts()
    if not all_aws_accounts:
        print("!!! ERROR: Tidak dapat memuat akun AWS dari file. Menghentikan.")
        return

    # Cari kredensial akun baru berdasarkan NEW_ACCOUNT_ID
    NEW_ACCOUNT_CREDS = None
    old_account_data = None
    new_account_index = -1
    old_account_index = -1

    for i, acc in enumerate(all_aws_accounts):
        if acc['id'] == NEW_ACCOUNT_ID:
            NEW_ACCOUNT_CREDS = acc
            new_account_index = i
        if acc['accessKey'] == OLD_ACCESS_KEY:
            old_account_data = acc
            old_account_index = i
    
    if not NEW_ACCOUNT_CREDS:
        print(f"!!! ERROR: Akun baru dengan ID '{NEW_ACCOUNT_ID}' tidak ditemukan di '{AWS_ACCOUNTS_FILE}'.")
        return
    if not old_account_data:
        print(f"!!! WARNING: Akun lama dengan Access Key '{OLD_ACCESS_KEY}' tidak ditemukan di '{AWS_ACCOUNTS_FILE}'. Mungkin sudah dihapus atau tidak pernah terdaftar.")
        # Lanjutkan saja, mungkin hanya membersihkan entri config.conf
    
    # Pastikan akun baru berstatus 'available'
    if NEW_ACCOUNT_CREDS['status'] != 'available':
        print(f"!!! ERROR: Akun baru '{NEW_ACCOUNT_ID}' tidak berstatus 'available'. Status saat ini: '{NEW_ACCOUNT_CREDS['status']}'.")
        print("Pastikan akun cadangan berstatus 'available' sebelum digunakan.")
        return

    # 3. Baca config.conf untuk menemukan instance yang perlu diganti
    if not os.path.exists(CONFIG_PATH):
        print(f"!!! ERROR: File konfigurasi '{CONFIG_PATH}' tidak ditemukan.")
        return

    config = configparser.ConfigParser()
    config.optionxform = str # Pertahankan kapitalisasi nama section
    config.read(CONFIG_PATH)

    sections_to_replace = []
    old_instance_ids_to_terminate = [] # Untuk terminasi instance lama

    for s in config.sections():
        # Hanya proses seksi numerik yang diasumsikan sebagai instance SOCKS
        if s.isdigit() and config.has_option(s, 'accessKey') and config.get(s, 'accessKey') == OLD_ACCESS_KEY:
            sections_to_replace.append({
                "section_name": s,
                "old_instance_id": config.get(s, 'instanceId') if config.has_option(s, 'instanceId') else None
            })
            if config.has_option(s, 'instanceId'):
                old_instance_ids_to_terminate.append(config.get(s, 'instanceId'))

    if not sections_to_replace:
        print(f"Tidak ada instance yang ditemukan di {CONFIG_PATH} menggunakan access key: {OLD_ACCESS_KEY}")
        print("Proses dihentikan.")
        # Jika tidak ada section yang perlu diganti, tetapi akun lama terdeteksi suspend
        # kita tetap perlu memperbarui status akun lama
        if old_account_index != -1:
            all_aws_accounts[old_account_index]['status'] = 'suspended'
            all_aws_accounts[old_account_index]['used_for_instances'] = []
            save_aws_accounts(all_aws_accounts)
        return

    print(f"Ditemukan {len(sections_to_replace)} instance untuk diganti pada seksi: {[s['section_name'] for s in sections_to_replace]}")
    if old_instance_ids_to_terminate:
        print(f"Instance lama yang akan dihentikan: {old_instance_ids_to_terminate}")

    # 4. Inisialisasi klien AWS untuk akun baru
    try:
        new_ec2_client = create_boto_client('ec2', NEW_ACCOUNT_CREDS)
        new_iam_client = create_boto_client('iam', NEW_ACCOUNT_CREDS) 
        new_ssm_client = create_boto_client('ssm', NEW_ACCOUNT_CREDS)
        
        # Bersihkan akun baru terlebih dahulu (opsional tapi disarankan)
        if not cleanup_existing_instances(new_ec2_client, thread_id="main_cleanup"):
            print("!!! Gagal membersihkan akun baru, proses dihentikan.")
            return

        ami_id = get_latest_amazon_linux_ami(new_ec2_client)
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code')
        if error_code in ['AuthFailure', 'InvalidClientTokenId', 'AccessDenied', 'UnauthorizedOperation']:
             print(f"!!! GAGAL saat inisialisasi akun baru '{NEW_ACCOUNT_ID}'. Akun baru ini mungkin SUSPENDED. Error: {e}")
             # Tandai akun baru sebagai suspended jika kredensialnya tidak valid
             all_aws_accounts[new_account_index]['status'] = 'suspended'
             save_aws_accounts(all_aws_accounts)
        else:
            print(f"!!! GAGAL saat inisialisasi akun baru. Pastikan kredensial benar. Error: {e}")
        return
    except Exception as e:
        print(f"!!! GAGAL saat inisialisasi akun baru. Error: {e}")
        return

    # 5. Jalankan penggantian instance secara paralel
    successful_replacements = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sections_to_replace)) as executor:
        future_to_section = {
            executor.submit(replace_one_section, s['section_name'], s['old_instance_id'], NEW_ACCOUNT_CREDS, new_ec2_client, new_iam_client, new_ssm_client, ami_id): s['section_name'] 
            for s in sections_to_replace
        }
        
        print(f"\n>>> Memulai {len(sections_to_replace)} proses penggantian secara paralel...")

        for future in concurrent.futures.as_completed(future_to_section):
            section_name_from_future = future_to_section[future] # Mendapatkan nama section dari future
            try:
                result = future.result()
                if result:
                    # Menambahkan data kredensial akun baru ke hasil untuk update config.conf
                    result['accessKey'] = NEW_ACCOUNT_CREDS['accessKey']
                    result['secretKey'] = NEW_ACCOUNT_CREDS['secretKey']
                    successful_replacements.append(result)
            except Exception as exc:
                print(f"!!! Seksi [{section_name_from_future}] menghasilkan exception: {exc}")

    if not successful_replacements:
        print("\nTidak ada penggantian yang berhasil dilakukan. File konfigurasi tidak diubah.")
        return

    print("\n>>> Semua thread selesai. Memperbarui konfigurasi di memori...")
    new_instances_for_account = [] # Untuk melacak instance ID baru di akun baru

    for replacement in successful_replacements:
        new_instances_for_account.append(replacement['new_instance_id'])

    # 6. Perbarui status di aws_accounts.json
    if old_account_index != -1:
        all_aws_accounts[old_account_index]['status'] = 'suspended'
        all_aws_accounts[old_account_index]['used_for_instances'] = [] # Kosongkan karena instance sudah tidak di sana
        print(f"  - Akun lama '{old_account_data['id']}' ({old_account_data['accessKey']}) ditandai 'suspended'.")

    if new_account_index != -1:
        all_aws_accounts[new_account_index]['status'] = 'active'
        all_aws_accounts[new_account_index]['used_for_instances'] = new_instances_for_account
        print(f"  - Akun baru '{NEW_ACCOUNT_CREDS['id']}' ditandai 'active' dengan instance {new_instances_for_account}.")
    
    save_aws_accounts(all_aws_accounts) # Simpan semua perubahan status

    # 7. Simpan semua perubahan ke file konfigurasi router & restart layanan
    # Panggil fungsi setup_router_server_redeploy dengan data sukses
    if not setup_router_server_redeploy(successful_replacements, CONFIG_PATH, NEW_ACCOUNT_CREDS):
        print("!!! ERROR: Gagal memperbarui config.conf dan me-restart layanan. Harap periksa log.")
        return # Hentikan jika gagal update/restart

    # 8. Hentikan instance lama di akun lama (jika ada dan bisa diakses)
    if old_instance_ids_to_terminate:
        print("\n>>> Mencoba menghentikan instance lama di akun lama...")
        try:
            # Pastikan old_account_data valid sebelum membuat klien
            if old_account_data and 'accessKey' in old_account_data and 'secretKey' in old_account_data:
                old_ec2_client_for_termination = create_boto_client('ec2', old_account_data) # Gunakan kredensial akun lama
                old_ec2_client_for_termination.terminate_instances(InstanceIds=old_instance_ids_to_terminate)
                print(f"  - Permintaan terminasi untuk instance lama ({old_instance_ids_to_terminate}) berhasil dikirim.")
            else:
                print("  - Tidak dapat menghentikan instance lama: Kredensial akun lama tidak ditemukan atau tidak valid.")
        except ClientError as e:
            print(f"  - PERINGATAN: Gagal menghentikan instance lama. Akun lama mungkin sudah sepenuhnya disuspend atau ada masalah lain. Error: {e}")
        except Exception as e:
            print(f"  - PERINGATAN: Gagal inisialisasi klien untuk menghentikan instance lama. Error: {e}")


    print("\n--- Semua Proses Penggantian Selesai ---")

if __name__ == '__main__':
    main()
