import boto3
import time
import json
import random
import string
import concurrent.futures
import os
import subprocess
import configparser
from botocore.exceptions import ClientError

# --- KONFIGURASI UTAMA ---
# Edit semua pengaturan di bagian ini sesuai kebutuhan Anda.

# 1. LOKASI FILE AKUN AWS
# Pastikan jalur ini sesuai dengan lokasi file aws_accounts.json yang Anda buat.
AWS_ACCOUNTS_FILE = "/opt/cloud-iprotate/aws_accounts.json" 
# Jika skrip berada di direktori yang sama dengan aws_accounts.json, bisa juga:
# AWS_ACCOUNTS_FILE = "aws_accounts.json"

# 2. KONFIGURASI DEPLOYMENT
AWS_REGION = "us-east-1"
INSTANCE_TYPE = "t2.micro"
VPS_USERNAME = "root"  # Username untuk login ke VPS Anda

# 3. KONFIGURASI SERVER & API (Digunakan jika config.conf belum ada)
# DIUBAH: key dihilangkan, hostPublicIp diubah menjadi placeholder
STATIC_CONFIG = """[api]
type = api
prefix = tj1
port = 3000
hostLocalIp = 0.0.0.0
hostPublicIp = ip vps ini
"""

# 4. PERINTAH INSTALASI
INSTALL_COMMAND = "sleep 10 && sudo curl -sSL https://raw.githubusercontent.com/harlock7771/cloud-iprotate/main/install-service.sh | sudo bash -s"

# --- FUNGSI BANTUAN ---

def load_aws_accounts():
    """Memuat daftar akun AWS dari file JSON."""
    if not os.path.exists(AWS_ACCOUNTS_FILE):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: File '{AWS_ACCOUNTS_FILE}' tidak ditemukan. Harap buat file ini.")
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

def generate_readable_name(prefix=""):
    """Menghasilkan nama acak yang mudah dibaca untuk resource AWS."""
    adjectives = ["agile", "bright", "calm", "deft", "eager", "fast", "gold", "happy", "icy", "jolly", "keen"]
    nouns = ["forest", "river", "sky", "wind", "storm", "sun", "moon", "field", "hill", "lake", "wave"]
    random_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    name = f"{random.choice(adjectives)}-{random.choice(nouns)}-{random_part}"
    return f"{prefix}-{name}" if prefix else name

def create_boto_client(service_name, account_creds):
    """Membuat klien boto3 untuk akun tertentu."""
    return boto3.client(
        service_name,
        aws_access_key_id=account_creds["accessKey"],
        aws_secret_access_key=account_creds["secretKey"],
        region_name=AWS_REGION
    )

def cleanup_existing_instances(ec2_client, thread_id):
    """Mencari dan menghapus semua instance yang ada di region target."""
    print(f"  - [Thread: {thread_id}] Memeriksa instance yang sudah ada...")
    try:
        response = ec2_client.describe_instances(
            Filters=[{'Name': 'instance-state-name', 'Values': ['pending', 'running', 'shutting-down', 'stopping', 'stopped']}]
        )
        instance_ids_to_terminate = [inst['InstanceId'] for res in response['Reservations'] for inst in res['Instances']]
        if not instance_ids_to_terminate:
            print(f"  - [Thread: {thread_id}] Akun bersih. Tidak ada instance untuk dihapus.")
            return
        print(f"  - [Thread: {thread_id}] Ditemukan {len(instance_ids_to_terminate)} instance yang akan dihapus: {instance_ids_to_terminate}")
        ec2_client.terminate_instances(InstanceIds=instance_ids_to_terminate)
        waiter = ec2_client.get_waiter('instance_terminated')
        waiter.wait(InstanceIds=instance_ids_to_terminate)
        print(f"  - [Thread: {thread_id}] Semua instance yang ada berhasil dihapus.")
    except ClientError as e:
        print(f"  - [Thread: {thread_id}] PERINGATAN: Gagal membersihkan instance. Error: {e}")

def get_instance_count_from_quota(ec2_client, sq_client, thread_id):
    """
    Menghitung jumlah instance yang dapat dibuat dan mengembalikan kuota vCPU mentah.
    """
    print(f"  - [Thread: {thread_id}] Mengecek kuota vCPU yang tersedia...")
    try:
        instance_type_info = ec2_client.describe_instance_types(InstanceTypes=[INSTANCE_TYPE])
        vcpus_per_instance = instance_type_info['InstanceTypes'][0]['VCpuInfo']['DefaultVCpus']
        quota_response = sq_client.get_service_quota(ServiceCode='ec2', QuotaCode='L-1216C47A')
        vcpu_limit = int(quota_response['Quota']['Value'])
        instance_count = vcpu_limit // vcpus_per_instance if vcpus_per_instance > 0 else 0
        print(f"    - [Thread: {thread_id}] Kuota vCPU Akun: {vcpu_limit} vCPU. Dapat membuat: {instance_count} instance {INSTANCE_TYPE}.")
        return instance_count, vcpu_limit
    except (ClientError, ValueError, KeyError) as e:
        if 'AccessDenied' in str(e) or 'AuthFailure' in str(e) or 'InvalidClientTokenId' in str(e):
            print(f"    - [Thread: {thread_id}] GAGAL: Akses ditolak saat memeriksa kuota. Kemungkinan akun ditangguhkan atau kredensial salah.")
            return 0, 0 # Anggap kuota 0 jika akses ditolak
        print(f"    - [Thread: {thread_id}] PERINGATAN: Gagal mengambil kuota vCPU. Error: {e}")
        return 0, None # Mengembalikan 0 instance dan None vcpu_limit jika ada error lain

def attempt_quota_increase(ec2_client, sq_client, thread_id):
    """
    Mencoba memicu kenaikan kuota jika kuota awal adalah 5 vCPU, dan memantaunya.
    """
    print(f"  - [Thread: {thread_id}] Kuota awal adalah 5 vCPU. Mencoba memicu kenaikan otomatis...")
    trigger_instance_type = "c5.4xlarge"  # Instance besar (16 vCPU) untuk memicu validasi
    
    try:
        ami_id = get_latest_amazon_linux_ami(ec2_client, quiet=True)
        print(f"  - [Thread: {thread_id}] Mencoba membuat instance {trigger_instance_type} untuk gagal (ini diharapkan)...")
        ec2_client.run_instances(ImageId=ami_id, InstanceType=trigger_instance_type, MinCount=1, MaxCount=1)
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code in ["VcpuLimitExceeded", "PendingVerification", "InsufficientInstanceCapacity"]:
            print(f"  - [Thread: {thread_id}] Pemicu berhasil ({error_code}). AWS seharusnya sekarang memvalidasi akun Anda.")
        else:
            print(f"  - [Thread: {thread_id}] Error tak terduga saat memicu. Melanjutkan dengan kuota 5 vCPU. Error: {e}")
            return 5 // 1 # Asumsi 1 vCPU per t2.micro
    except Exception as e:
        print(f"  - [Thread: {thread_id}] Error umum saat memicu kenaikan kuota: {e}. Melanjutkan dengan kuota 5 vCPU.")
        return 5 // 1

    # Fase Pemantauan
    print(f"  - [Thread: {thread_id}] Memasuki fase pemantauan kuota...")
    polling_timeout = time.time() + 60 * 5  # Timeout 5 menit
    while time.time() < polling_timeout:
        print(f"  - [Thread: {thread_id}] Menunggu 60 detik sebelum memeriksa ulang kuota...")
        time.sleep(60)
        instance_count, new_vcpu_limit = get_instance_count_from_quota(ec2_client, sq_client, thread_id)
        if new_vcpu_limit is not None and new_vcpu_limit > 5:
            print(f"  - [Thread: {thread_id}] SUKSES! Kuota telah meningkat menjadi {new_vcpu_limit} vCPU.")
            return instance_count
        if new_vcpu_limit == 0: # Akun disuspend saat polling
            print(f"  - [Thread: {thread_id}] AKUN DISUSPEND saat menunggu kenaikan kuota. Menghentikan proses.")
            return 0
    
    print(f"  - [Thread: {thread_id}] TIMEOUT: Kuota tidak meningkat setelah 5 menit. Akan melanjutkan dengan kuota yang ada.")
    final_count, _ = get_instance_count_from_quota(ec2_client, sq_client, thread_id)
    return final_count


def create_ssm_role_and_profile(iam_client, role_name, profile_name):
    """Membuat IAM Role dan Instance Profile untuk SSM."""
    print(f"  - Memeriksa atau membuat IAM Role: {role_name}")
    trust_policy = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    try:
        iam_client.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust_policy)
        time.sleep(10)
    except ClientError as e:
        if e.response['Error']['Code'] != 'EntityAlreadyExists': raise e
    iam_client.attach_role_policy(RoleName=role_name, PolicyArn='arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore')
    
    print(f"  - Memeriksa atau membuat Instance Profile: {profile_name}")
    try:
        iam_client.create_instance_profile(InstanceProfileName=profile_name)
    except ClientError as e:
        if e.response['Error']['Code'] != 'EntityAlreadyExists': raise e
    try:
        iam_client.add_role_to_instance_profile(InstanceProfileName=profile_name, RoleName=role_name)
    except ClientError as e:
        if e.response['Error']['Code'] != 'LimitExceeded': raise e
    time.sleep(15)

def create_and_configure_security_group(ec2_client, sg_name):
    """Membuat Security Group yang mengizinkan semua lalu lintas."""
    print(f"  - Memeriksa atau membuat Security Group: {sg_name}")
    try:
        vpc_id = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])['Vpcs'][0]['VpcId']
        response = ec2_client.describe_security_groups(Filters=[{'Name': 'group-name', 'Values': [sg_name]}])
        if response['SecurityGroups']: return response['SecurityGroups'][0]['GroupId']
        sg_id = ec2_client.create_security_group(GroupName=sg_name, Description='Allow all traffic', VpcId=vpc_id)['GroupId']
        ec2_client.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{'IpProtocol': '-1', 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}])
        return sg_id
    except ClientError as e:
        raise e

def get_latest_amazon_linux_ami(ec2_client, quiet=False):
    """Mencari AMI Amazon Linux 2023 terbaru."""
    if not quiet: print("  - Mencari AMI Amazon Linux 2023 terbaru...")
    response = ec2_client.describe_images(Owners=['amazon'], Filters=[
        {'Name': 'name', 'Values': ['al2023-ami-2023.*-kernel-6.1-x86_64']},
        {'Name': 'state', 'Values': ['available']}, {'Name': 'architecture', 'Values': ['x86_64']}
    ])
    images = sorted(response['Images'], key=lambda x: x['CreationDate'], reverse=True)
    if not images: raise Exception("Tidak ada AMI Amazon Linux 2023 yang ditemukan.")
    if not quiet: print(f"    - AMI ditemukan: {images[0]['ImageId']}")
    return images[0]['ImageId']

def launch_instances(ec2_client, ami_id, profile_name, sg_id, tag_key, tag_value, instance_count):
    """Membuat instance EC2 dengan jumlah yang ditentukan."""
    if instance_count <= 0:
        print("    - Tidak ada instance yang akan dibuat karena jumlahnya 0.")
        return []
    print(f"  - Membuat {instance_count} instance EC2 tipe {INSTANCE_TYPE}...")
    response = ec2_client.run_instances(
        ImageId=ami_id, InstanceType=INSTANCE_TYPE,
        MinCount=instance_count, MaxCount=instance_count,
        IamInstanceProfile={'Name': profile_name},
        SecurityGroupIds=[sg_id],
        TagSpecifications=[{'ResourceType': 'instance', 'Tags': [{'Key': tag_key, 'Value': tag_value}]}]
    )
    instance_ids = [inst['InstanceId'] for inst in response['Instances']]
    waiter = ec2_client.get_waiter('instance_running')
    waiter.wait(InstanceIds=instance_ids)
    print(f"    - {len(instance_ids)} instance sudah 'running'.")
    return instance_ids

def wait_for_ssm_registration(ssm_client, instance_ids):
    """Menunggu instance terdaftar di SSM."""
    if not instance_ids: return
    print("  - Menunggu instance terdaftar di SSM...")
    timeout = time.time() + 60 * 10
    while time.time() < timeout:
        response = ssm_client.describe_instance_information(Filters=[{'Key': 'InstanceIds', 'Values': instance_ids}])
        if len(response.get('InstanceInformationList', [])) == len(instance_ids) and \
           all(info.get('PingStatus') == 'Online' for info in response['InstanceInformationList']):
            print("    - Semua instance berhasil terdaftar dan Online di SSM.")
            return True
        time.sleep(30)
    print("!!! Timeout: Tidak semua instance terdaftar di SSM dalam 10 menit.")
    return False

def run_command_on_instances(ssm_client, instance_ids, tag_key, tag_value):
    """Menjalankan perintah instalasi pada seluruh instance yang bertag sama."""
    if not instance_ids:
        return True # Tidak ada instance, jadi anggap sukses
    
    if not wait_for_ssm_registration(ssm_client, instance_ids):
        return False # Gagal terdaftar di SSM, tidak bisa jalankan perintah

    print("  - Menjalankan perintah instalasi via tag...")

    # Kirim sekali ke semua instance yang punya tag <tag_key>=<tag_value>
    try:
        response = ssm_client.send_command(
            DocumentName='AWS-RunShellScript',
            Targets=[{'Key': f'tag:{tag_key}', 'Values': [tag_value]}],
            Parameters={'commands': [INSTALL_COMMAND]},
            TimeoutSeconds=600
        )
        command_id = response['Command']['CommandId']

        # Tunggu hingga semua instance menyelesaikan perintah
        # Waiter untuk command_executed adalah per instance. Kita harus polling.
        print("  - Menunggu perintah instalasi selesai pada semua instance...")
        timeout = time.time() + 60 * 15 # 15 menit timeout untuk instalasi
        while time.time() < timeout:
            all_successful = True
            for inst_id in instance_ids:
                status_response = ssm_client.get_command_invocation(CommandId=command_id, InstanceId=inst_id)
                status = status_response['Status']
                if status == 'Pending' or status == 'InProgress':
                    all_successful = False
                    break # Lanjut polling
                elif status == 'Failed' or status == 'Cancelled' or status == 'TimedOut':
                    print(f"!!! Perintah instalasi pada {inst_id} GAGAL: {status}")
                    print(f"    - Output: {status_response.get('StandardOutputContent', 'N/A')}")
                    print(f"    - Error: {status_response.get('StandardErrorContent', 'N/A')}")
                    return False # Ada yang gagal, seluruh proses gagal
            
            if all_successful:
                print("  - Semua perintah instalasi selesai dengan sukses.")
                return True
            
            time.sleep(30) # Tunggu sebelum polling lagi
        
        print("!!! TIMEOUT: Perintah instalasi tidak selesai dalam 15 menit.")
        return False

    except ClientError as e:
        print(f"  - GAGAL mengirim perintah instalasi: {e}")
        return False


def setup_router_server(all_instances_data, config_file_path):
    """
    Menghasilkan file konfigurasi dan me-restart layanan router.
    Sekarang menerima path config_file_path sebagai argumen.
    """
    print("\n>>> Mengonfigurasi VPS Server Lokal <<<")
    config = configparser.ConfigParser()
    config.optionxform = str # Pertahankan kapitalisasi nama section
    existing_content = ""
    
    # Baca konfigurasi yang sudah ada
    if os.path.exists(config_file_path):
        with open(config_file_path, 'r') as f:
            existing_content = f.read()
        config.read_string(existing_content)
    
    # Tentukan port awal
    # Kumpulkan semua port yang sudah ada untuk memastikan tidak ada duplikasi
    existing_ports = set()
    for section in config.sections():
        if section.isdigit(): # Anggap section numerik adalah port
            try:
                existing_ports.add(int(section))
            except ValueError:
                pass # Abaikan jika bukan angka
    
    next_port = 2000 # Port awal default (atau bisa 2001 seperti contoh Anda)
    if existing_ports:
        next_port = max(existing_ports) + 1

    for i, data in enumerate(all_instances_data):
        current_port = next_port + i
        
        # Tambahkan ke konfigurasi dalam memori
        section_name = str(current_port)
        config[section_name] = {
            'type': 'aws',
            'socks5Port': str(current_port),
            'httpPort': str(current_port + 1000), # Disesuaikan dengan format yang Anda inginkan (socks5Port + 1000)
            'accessKey': data['accessKey'],
            'secretKey': data['secretKey'],
            'instanceId': data['instanceId'],
            'region': AWS_REGION
        }

    # Pastikan bagian [api] ada jika ini adalah inisialisasi pertama
    if not config.has_section('api'):
        temp_config = configparser.ConfigParser()
        temp_config.optionxform = str
        temp_config.read_string(STATIC_CONFIG) # STATIC_CONFIG yang sudah tanpa key dan dengan placeholder IP
        for section in temp_config.sections():
            if not config.has_section(section):
                config[section] = temp_config[section]

    temp_path = "/tmp/config.conf.tmp"
    with open(temp_path, 'w') as f:
        config.write(f) # Menulis objek config, bukan string lagi

    subprocess.run(f"sudo mkdir -p {os.path.dirname(config_file_path)} && sudo mv {temp_path} {config_file_path}", shell=True, check=True)
    print("  - File konfigurasi berhasil disimpan.")
    
    # Menggunakan pm2 restart index.js --name iprotate sesuai kebiasaan Anda
    subprocess.run("sudo pm2 restart index.js --name iprotate", shell=True, check=True)
    print("  - Layanan router berhasil di-restart.")

def prompt_for_backup(config_file_path):
    """Memberikan pengguna perintah SCP untuk mem-backup file konfigurasi."""
    print("\n--- PENTING: Backup Konfigurasi Anda ---")
    
    if not os.path.exists(config_file_path):
        print("  - File konfigurasi tidak ditemukan, backup dilewati.")
        return
        
    config = configparser.ConfigParser()
    config.read(config_file_path)
    
    try:
        vps_ip = config.get('api', 'hostPublicIp')
        
        print("  - Deployment selesai! Untuk menyimpan cadangan konfigurasi Anda,")
        print("    jalankan perintah berikut dari terminal di PC PRIBADI Anda (PowerShell/CMD):")
        print("\n    ==============================================================================================")
        print(f"    scp {VPS_USERNAME}@{vps_ip}:{config_file_path} C:\\Users\\TEJO\\Desktop\\backupAWS\\config-backup.txt")
        print("    ==============================================================================================")
        print("\n    * Ganti 'TEJO' dengan username folder di PC Anda.")
        print("    * Anda bisa mengubah folder tujuan dan nama file backup sesuai keinginan.")
        
    except (configparser.NoSectionError, configparser.NoOptionError):
        print(f"  - Gagal membuat perintah backup. Pastikan [api] dan hostPublicIp ada di {config_file_path}")


def deploy_on_account(account_index, all_accounts_data):
    """Fungsi worker yang menjalankan seluruh proses deployment untuk SATU akun."""
    account = all_accounts_data[account_index]
    thread_id = account['id']
    print(f"\n>>> [Thread: {thread_id}] Memulai proses deployment...")
    
    # Hanya proses akun dengan status 'active' atau 'available'
    if account['status'] not in ["active", "available"]:
        print(f"--- [Thread: {thread_id}] Melewatkan akun ini karena statusnya '{account['status']}'.")
        return []

    try:
        run_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        role_name, profile_name, sg_name = f"ssm-role-{run_id}", f"ssm-profile-{run_id}", f"deploy-sg-{run_id}"
        tag_key, tag_value = generate_readable_name("project"), generate_readable_name()

        iam_client = create_boto_client('iam', account)
        ec2_client = create_boto_client('ec2', account)
        ssm_client = create_boto_client('ssm', account)
        sq_client = create_boto_client('service-quotas', account)

        instance_count_to_create, vcpu_limit = get_instance_count_from_quota(ec2_client, sq_client, thread_id)
        
        # Logika baru untuk menandai akun yang ditangguhkan.
        if vcpu_limit == 0:
            print(f"!!! [Thread: {thread_id}] Akun terdeteksi sebagai SUSPENDED (kuota vCPU adalah 0).")
            all_accounts_data[account_index]['status'] = 'suspended'
            save_aws_accounts(all_accounts_data)
            return []

        # Logika untuk menangani kuota rendah
        if vcpu_limit is not None and vcpu_limit > 0 and vcpu_limit <= 5: # Jika kuota 1-5 (biasanya 5)
            instance_count_to_create = attempt_quota_increase(ec2_client, sq_client, thread_id)
            if instance_count_to_create == 0: # Gagal pemicu atau akun disuspend saat polling
                print(f"!!! [Thread: {thread_id}] Gagal meningkatkan kuota atau akun disuspend. Melewatkan akun ini.")
                all_accounts_data[account_index]['status'] = 'suspended' # Tandai sebagai suspended
                save_aws_accounts(all_accounts_data)
                return []
        
        # Pembersihan instance hanya dilakukan jika akun valid (kuota > 0)
        cleanup_existing_instances(ec2_client, thread_id)
        
        if instance_count_to_create <= 0:
            print(f"  - [Thread: {thread_id}] Tidak ada instance yang akan dibuat berdasarkan kuota. Menghentikan proses untuk akun ini.")
            return []
            
        create_ssm_role_and_profile(iam_client, role_name, profile_name)
        sg_id = create_and_configure_security_group(ec2_client, sg_name)
        ami_id = get_latest_amazon_linux_ami(ec2_client)
        instance_ids = launch_instances(ec2_client, ami_id, profile_name, sg_id, tag_key, tag_value, instance_count_to_create)
        
        if not run_command_on_instances(ssm_client, instance_ids, tag_key, tag_value):
            print(f"!!! [Thread: {thread_id}] Gagal menjalankan perintah instalasi pada instance. Menghentikan proses.")
            # Pertimbangkan untuk menghentikan instance yang gagal
            try:
                if instance_ids:
                    ec2_client.terminate_instances(InstanceIds=instance_ids)
                    print(f"  - [Thread: {thread_id}] Instance yang gagal di-setup telah dihentikan.")
            except ClientError as e:
                print(f"  - [Thread: {thread_id}] PERINGATAN: Gagal menghentikan instance yang gagal. Error: {e}")
            return []

        launched_instances_data = []
        for inst_id in instance_ids:
            launched_instances_data.append({
                "instanceId": inst_id,
                "accessKey": account["accessKey"],
                "secretKey": account["secretKey"],
                "accountId": account["id"] # Tambahkan ID akun untuk pelacakan
            })
            # Update used_for_instances di aws_accounts.json
            all_accounts_data[account_index]['used_for_instances'].append(inst_id)
        
        all_accounts_data[account_index]['status'] = 'active' # Pastikan statusnya aktif
        save_aws_accounts(all_accounts_data) # Simpan perubahan ke file JSON
        
        print(f">>> [Thread: {thread_id}] Proses Selesai. Instance diluncurkan: {len(instance_ids)}")
        return launched_instances_data

    except Exception as e:
        print(f"!!! [Thread: {thread_id}] GAGAL: {e}")
        # Jika terjadi error fatal, tandai akun sebagai 'error' atau 'suspended' jika relevan
        # Namun, di sini kita hanya mengembalikan kosong untuk mencegah penghentian seluruh proses
        all_accounts_data[account_index]['status'] = 'error' # Tandai sebagai error
        save_aws_accounts(all_accounts_data)
        return []

def main_deployer(): # Mengganti nama fungsi main menjadi main_deployer untuk kejelasan
    print("--- Memulai Proses Auto-Deployment End-to-End ---")
    
    # Muat semua akun dari file JSON
    all_aws_accounts = load_aws_accounts()
    if not all_aws_accounts:
        return

    # Filter hanya akun yang berstatus 'active' atau 'available' untuk deployment awal
    # Atau jika ingin hanya primary untuk awal (8 akun pertama), bisa difilter di sini:
    # accounts_to_deploy_indices = [i for i, acc in enumerate(all_aws_accounts) 
    #                               if acc['id'].startswith('primary_aws_')]
    # Saat ini, akan mencoba semua yang 'active' atau 'available'.
    accounts_to_deploy_indices = [i for i, acc in enumerate(all_aws_accounts) 
                                  if acc['status'] in ["active", "available"]]

    if not accounts_to_deploy_indices:
        print("\nTidak ada akun 'active' atau 'available' yang ditemukan untuk deployment.")
        return

    all_launched_instances_data = []
    
    # Gunakan ThreadPoolExecutor untuk menjalankan deployment secara paralel
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(accounts_to_deploy_indices)) as executor:
        future_to_account_index = {executor.submit(deploy_on_account, idx, all_aws_accounts): idx 
                                   for idx in accounts_to_deploy_indices}
        
        for future in concurrent.futures.as_completed(future_to_account_index):
            account_idx = future_to_account_index[future]
            try:
                # Mengambil hasil dari setiap thread. future.result() akan mengembalikan [] jika gagal.
                result_instances = future.result()
                all_launched_instances_data.extend(result_instances)
            except Exception as e:
                # Ini menangani pengecualian tak terduga dari dalam deploy_on_account
                print(f"!!! Terjadi exception pada thread untuk akun index {account_idx}: {e}")
                all_aws_accounts[account_idx]['status'] = 'error'
                save_aws_accounts(all_aws_accounts)

    if not all_launched_instances_data:
        print("\nTidak ada instance yang berhasil dibuat. Proses dihentikan.")
        return

    # Path untuk config.conf, bisa disesuaikan jika berbeda
    ROUTER_CONFIG_PATH = "/opt/cloud-iprotate/config.conf"

    # Perbarui config.conf hanya dengan instance yang berhasil diluncurkan
    setup_router_server(all_launched_instances_data, ROUTER_CONFIG_PATH)
    
    # Menampilkan perintah backup setelah semua proses server selesai
    prompt_for_backup(ROUTER_CONFIG_PATH)
    
    print("\n--- Semua Proses Deployment Selesai ---")

if __name__ == '__main__':
    main_deployer()
