#!/bin/bash

# --- BARU: Pindah ke direktori aman sebelum operasi destruktif ---
# Ini mencegah error 'getcwd' jika direktori eksekusi skrip dihapus.
cd /tmp || { echo "Gagal pindah ke /tmp. Keluar."; exit 1; }
# --- AKHIR BARU ---

# Pastikan skrip dijalankan sebagai root atau dengan sudo
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root or with sudo"
  exit 1
fi

echo "Memulai instalasi dependencies dasar..."
apt update -y
apt install -y tar screen wget curl nano htop git --no-install-recommends

echo "Memeriksa dan menginstal Node.js versi 18.x..."
if ! node -v | grep -q "v18"; then
    curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
    apt update -y
    apt install -y nodejs
    npm install -g n
    npm install -g pm2
    n 18
else
    echo "Node.js version 18.x.x already installed"
fi

echo "Membersihkan instalasi lama dan menyiapkan direktori /opt/cloud-iprotate/..."
# PERHATIAN PENTING: Perintah ini akan MENGHAPUS SELURUH ISI direktori /opt/cloud-iprotate/
# Jika Anda meng-upgrade instalasi yang sudah ada dan memiliki config.conf atau aws_accounts.json
# yang sudah terisi data penting, data tersebut AKAN HILANG.
# Pastikan Anda sudah mem-backupnya jika diperlukan, atau hanya jalankan skrip ini
# pada instalasi VPS yang benar-benar baru.
if [[ -d "/opt/cloud-iprotate/" ]]; then
    echo "Direktori /opt/cloud-iprotate/ ditemukan, menghapus untuk instalasi bersih..."
    rm -rf /opt/cloud-iprotate/
else
    echo "Direktori /opt/cloud-iprotate/ tidak ditemukan, melanjutkan pembuatan..."
fi


# Buat direktori utama untuk aplikasi
sudo mkdir -p /opt/cloud-iprotate/ || { echo "Gagal membuat direktori /opt/cloud-iprotate/. Keluar."; exit 1; }
sudo chown -R $(logname):$(logname) /opt/cloud-iprotate/ # Ubah kepemilikan ke user yang menjalankan sudo
chmod -R 755 /opt/cloud-iprotate/ # Beri izin yang sesuai

echo "Mengunduh aplikasi router Node.js (index.js, configtemplate.json, dan package.json)..."
sudo curl -sSL https://raw.githubusercontent.com/harlock7771/cloud-iprotate/main/index.js -o /opt/cloud-iprotate/index.js || { echo "Gagal mengunduh index.js. Keluar."; exit 1; }
sudo curl -sSL https://raw.githubusercontent.com/harlock7771/cloud-iprotate/main/configtemplate.json -o /opt/cloud-iprotate/configtemplate.json || { echo "Gagal mengunduh configtemplate.json. Keluar."; exit 1; }
sudo curl -sSL https://raw.githubusercontent.com/harlock7771/cloud-iprotate/main/package.json -o /opt/cloud-iprotate/package.json || { echo "Gagal mengunduh package.json. Keluar."; exit 1; }


# --- BARIS-BARIS UNTUK SKRIP PYTHON DAN FILE AWS_ACCOUNTS.JSON ---
echo "Mengunduh skrip Python (`main.py`, `health_monitor.py`, `redeploy.py`) dan file `aws_accounts.json`..."
sudo curl -sSL https://raw.githubusercontent.com/harlock7771/cloud-iprotate/main/main.py -o /opt/cloud-iprotate/main.py || { echo "Gagal mengunduh main.py. Keluar."; exit 1; }
sudo curl -sSL https://raw.githubusercontent.com/harlock7771/cloud-iprotate/main/health_monitor.py -o /opt/cloud-iprotate/health_monitor.py || { echo "Gagal mengunduh health_monitor.py. Keluar."; exit 1; }
sudo curl -sSL https://raw.githubusercontent.com/harlock7771/cloud-iprotate/main/redeploy.py -o /opt/cloud-iprotate/redeploy.py || { echo "Gagal mengunduh redeploy.py. Keluar."; exit 1; }
sudo curl -sSL https://raw.githubusercontent.com/harlock7771/cloud-iprotate/main/aws_accounts.json -o /opt/cloud-iprotate/aws_accounts.json || { echo "Gagal mengunduh aws_accounts.json. Keluar."; exit 1; }

echo "Menginstal dependensi Python..."
sudo apt install python3 python3-pip -y
pip3 install boto3

echo "Mengatur izin eksekusi untuk skrip Python..."
sudo chmod +x /opt/cloud-iprotate/main.py
sudo chmod +x /opt/cloud-iprotate/health_monitor.py
sudo chmod +x /opt/cloud-iprotate/redeploy.py
# --- AKHIR PENAMBAHAN ---

echo "Menginstal dependensi Node.js untuk aplikasi router..."
# Pastikan npm install dijalankan di direktori yang benar
(cd /opt/cloud-iprotate/ && npm install) || { echo "Gagal menjalankan npm install. Keluar."; exit 1; }

echo "Menyiapkan PM2 untuk aplikasi router (index.js)..."
# Menggunakan --cwd untuk secara eksplisit menentukan direktori kerja PM2
pm2 start /opt/cloud-iprotate/index.js --name iprotate --cwd /opt/cloud-iprotate/ || { echo "Gagal memulai aplikasi dengan PM2. Keluar."; exit 1; }
pm2 save || { echo "Gagal menyimpan konfigurasi PM2. Keluar."; exit 1; }
pm2 startup systemd || { echo "Gagal mengaktifkan PM2 startup. Keluar."; exit 1; }

echo "Instalasi router selesai. Anda siap!"
echo "Langkah selanjutnya: Jalankan 'python3 /opt/cloud-iprotate/main.py' untuk deployment awal."
echo "PENTING: Setelah 'main.py' berjalan, edit /opt/cloud-iprotate/config.conf dan ganti 'hostPublicIp = 123.45.67.89' dengan IP publik VPS Anda."
echo "Kemudian, atur cron job untuk health_monitor.py."
