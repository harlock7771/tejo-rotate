import boto3
import time
import json
import os
import subprocess
from botocore.exceptions import ClientError

# --- KONFIGURASI MONITOR ---
AWS_ACCOUNTS_FILE = "/opt/cloud-iprotate/aws_accounts.json"
# Pastikan jalur ini sesuai dengan lokasi file aws_accounts.json yang Anda buat.

# Path ke skrip redeploy.py Anda
REDEPLOY_SCRIPT_PATH = "/opt/cloud-iprotate/redeploy.py" 
# Jika skrip berada di direktori yang sama, bisa juga:
# REDEPLOY_SCRIPT_PATH = "redeploy.py"

AWS_REGION = "us-east-1" # Pastikan ini sama dengan region di main.py dan redeploy.py

# Interval pemeriksaan kesehatan (dalam detik). Misalnya, 300 detik = 5 menit.
CHECK_INTERVAL_SECONDS = 600

# --- FUNGSI BANTUAN (DIADAPTASI DARI MAIN.PY) ---

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

def check_account_health(account_creds):
    """
    Memeriksa kesehatan akun AWS dengan mencoba operasi sederhana.
    Mengembalikan True jika sehat, False jika ada masalah (termasuk suspend).
    """
    account_id = account_creds['id']
    try:
        ec2_client = create_boto_client('ec2', account_creds)
        # Coba deskripsikan satu instance saja, atau bahkan hanya region untuk cek akses
        ec2_client.describe_regions() # Operasi ringan untuk cek kredensial
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] INFO: Akun '{account_id}' terlihat sehat.")
        return True
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code')
        # Common errors for suspended/invalid credentials
        if error_code in ['AuthFailure', 'InvalidClientTokenId', 'AccessDenied', 'UnauthorizedOperation']:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] WARNING: Akun '{account_id}' terdeteksi bermasalah (Code: {error_code}). Kemungkinan SUSPENDED.")
            return False
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Gagal memeriksa akun '{account_id}' karena error tak terduga: {e}")
            return False
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Terjadi pengecualian saat memeriksa akun '{account_id}': {e}")
        return False

def find_available_backup_account(all_accounts):
    """Mencari akun cadangan pertama yang berstatus 'available'."""
    for account in all_accounts:
        if account['status'] == 'available':
            return account
    return None

def trigger_redeploy(old_account_access_key, new_account_creds):
    """
    Memicu skrip redeploy.py sebagai proses terpisah.
    Ini akan kita modifikasi nanti agar redeploy.py bisa menerima argumen.
    """
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] INFO: Memicu redeploy.py untuk menggantikan akun '{old_account_access_key}' dengan '{new_account_creds['id']}'.")
    
    try:
        # Menjalankan redeploy.py sebagai subprocess dengan argumen
        # Kita akan mengubah redeploy.py untuk menerima argumen ini di langkah berikutnya
        # Untuk saat ini, kita akan melewati access key lama dan access key baru sebagai argumen.
        # Kemudian redeploy.py akan mencari secret key-nya sendiri dari aws_accounts.json.
        
        # Contoh perintah (ini akan disesuaikan di redeploy.py)
        command = [
            "python3", REDEPLOY_SCRIPT_PATH,
            "--old-access-key", old_account_access_key,
            "--new-account-id", new_account_creds['id']
        ]
        
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] INFO: Menjalankan perintah: {' '.join(command)}")
        
        # run() akan menunggu perintah selesai. Gunakan Popen jika ingin non-blocking.
        # Untuk monitoring, menunggu sampai selesai lebih baik agar status bisa diperbarui setelahnya.
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        
        if result.returncode == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] INFO: redeploy.py berhasil dijalankan.")
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] redeploy.py stdout:\n{result.stdout}")
            if result.stderr:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] redeploy.py stderr:\n{result.stderr}")
            return True
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: redeploy.py gagal dijalankan (Exit Code: {result.returncode}).")
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] redeploy.py stdout:\n{result.stdout}")
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] redeploy.py stderr:\n{result.stderr}")
            return False
            
    except FileNotFoundError:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Skrip redeploy.py tidak ditemukan di '{REDEPLOY_SCRIPT_PATH}'.")
        return False
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Gagal memicu redeploy.py: {e}")
        return False

def main_monitor():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] --- Memulai Health Monitor ---")
    
    while True:
        all_accounts = load_aws_accounts()
        if not all_accounts:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Tidak ada akun yang dimuat. Menunggu {CHECK_INTERVAL_SECONDS} detik.")
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        accounts_modified = False
        
        for i, account in enumerate(all_accounts):
            # Hanya periksa akun yang berstatus 'active'
            if account['status'] == 'active':
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Memeriksa kesehatan akun: {account['id']}")
                if not check_account_health(account):
                    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Akun '{account['id']}' terdeteksi TIDAK SEHAT.")
                    
                    # Tandai akun ini sebagai 'suspended'
                    all_accounts[i]['status'] = 'suspended'
                    accounts_modified = True
                    save_aws_accounts(all_accounts) # Simpan perubahan status segera

                    # Cari akun cadangan yang tersedia
                    new_backup_account = find_available_backup_account(all_accounts)
                    
                    if new_backup_account:
                        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Ditemukan akun cadangan: '{new_backup_account['id']}'.")
                        
                        # Panggil skrip redeploy.py
                        if trigger_redeploy(account['accessKey'], new_backup_account):
                            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Redeploy berhasil untuk akun '{account['id']}'.")
                            
                            # Setelah redeploy berhasil, tandai akun cadangan sebagai 'active'
                            # Kita perlu mencari indeks akun cadangan ini
                            for j, bk_acc in enumerate(all_accounts):
                                if bk_acc['id'] == new_backup_account['id']:
                                    all_accounts[j]['status'] = 'active'
                                    all_accounts[j]['used_for_instances'] = [] # Akan diisi oleh redeploy.py
                                    break
                            accounts_modified = True
                        else:
                            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Redeploy gagal untuk akun '{account['id']}'. Akun tidak diganti.")
                    else:
                        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Tidak ada akun cadangan yang 'available'. Tidak dapat mengganti akun '{account['id']}'.")
                        
        # Simpan perubahan terakhir jika ada modifikasi yang belum tersimpan
        if accounts_modified:
            save_aws_accounts(all_accounts)

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Selesai memeriksa. Menunggu {CHECK_INTERVAL_SECONDS} detik untuk pemeriksaan berikutnya.")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == '__main__':
    main_monitor()
