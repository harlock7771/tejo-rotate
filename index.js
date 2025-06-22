const express = require('express')
const app = express()
const bodyParser = require('body-parser')
const fs = require('fs')
const morgan = require('morgan')
const axios = require('axios')
const { performance } = require('perf_hooks')
const { SocksProxyAgent } = require('socks-proxy-agent')
const tencentcloud = require('tencentcloud-sdk-nodejs')
const { DefaultAzureCredential } = require('@azure/identity')
const { NetworkManagementClient } = require('@azure/arm-network')
const {
  EC2Client,
  AllocateAddressCommand,
  DisassociateAddressCommand,
  AssociateAddressCommand,
  DescribeAddressesCommand,
  ReleaseAddressCommand,
  DescribeInstancesCommand,
} = require('@aws-sdk/client-ec2')
const ConfigParser = require('configparser')
const { exec } = require('child_process')
app.use(bodyParser.json())
app.use(morgan('combined'))
const config = new ConfigParser()
config.read('config.conf')
const prefix = config.get('api', 'prefix')
const appPort = config.get('api', 'port')
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
//clearup runningprocess.txt file
fs.writeFile('runningprocess.txt', '', function (err) {
  if (err) throw err
})

app.listen(appPort, '0.0.0.0', () => {
  console.log(`Listening on port ${appPort}`)
})

// --- FUNGSI newIpAws, newIpAzure, newIpTencent TETAP SAMA ---
// (Tidak perlu diubah, jadi saya singkat di sini agar tidak terlalu panjang)
async function newIpAws(serverConfig) {
  /* ... kode asli ... */
}
async function newIpAzure(serverConfig) {
  /* ... kode asli ... */
}
async function newIpTencent(serverConfig) {
  /* ... kode asli ... */
}
// --- AKHIR DARI FUNGSI YANG TIDAK BERUBAH ---

// --- FUNGSI parseConfig DIMODIFIKASI UNTUK MENGHILANGKAN CLOUDFLARE ---
async function parseConfig() {
  config.read('config.conf')
  const confList = config.sections()
  let tencentConfigList = []
  let azureConfigList = []
  let awsConfigList = []
  // cloudflareConfig tidak lagi dibutuhkan
  // let cloudflareConfig = {} // DIHAPUS
  let apiConfig = {}
  for (let i = 0; i < confList.length; i++) {
    const configName = confList[i]
    const configType = config.get(confList[i], 'type')
    if (configType == 'tencent') {
        // ... (kode asli tidak berubah)
    } 
    // Bagian 'cloudflare' dihapus seluruhnya
    /* else if (configType == 'cloudflare') { // DIHAPUS
      const email = config.get(confList[i], 'email')
      const token = config.get(confList[i], 'token')
      const domain = config.get(confList[i], 'domain')
      const zoneId = config.get(confList[i], 'zoneId') || ''
      const configName = confList[i]
      cloudflareConfig.email = email
      cloudflareConfig.token = token
      cloudflareConfig.domain = domain
      cloudflareConfig.configName = configName
      cloudflareConfig.zoneId = zoneId
    } */
    else if (configType == 'api') {
      const prefix = config.get(confList[i], 'prefix')
      const port = config.get(confList[i], 'port')
      const hostLocalIp = config.get(confList[i], 'hostLocalIp')
      const hostPublicIp = config.get(confList[i], 'hostPublicIp')
      const key = config.get(confList[i], 'key')
      // const apiHostName = config.get(confList[i], 'apiHostName') // DIHAPUS
      apiConfig.prefix = prefix
      apiConfig.port = port
      apiConfig.hostLocalIp = hostLocalIp
      apiConfig.hostPublicIp = hostPublicIp
      apiConfig.key = key
      // apiConfig.apiHostName = apiHostName // DIHAPUS
    } else if (configType == 'azure') {
        // ... (kode asli tidak berubah)
    } else if (configType == 'aws') {
        // ... (kode asli tidak berubah)
    }
  }
  return {
    configs: {
      api: apiConfig,
      // cloudflare: cloudflareConfig, // DIHAPUS
      tencent: tencentConfigList,
      azure: azureConfigList,
      aws: awsConfigList,
    },
  }
}

// --- FUNGSI checkCloudflare, checkTencent, checkAws, checkAzure TETAP SAMA ---
// (Tidak perlu diubah, jadi saya singkat di sini)
async function checkCloudflare(serverConfig) { /*... kode asli ...*/ }
async function checkTencent(serverConfig) { /*... kode asli ...*/ }
async function checkAws(serverConfig) { /*... kode asli ...*/ }
async function checkAzure(serverConfig) { /*... kode asli ...*/ }
// --- AKHIR DARI FUNGSI YANG TIDAK BERUBAH ---


app.get(`/${prefix}/newip/`, async (req, res) => {
  const startTime = performance.now()
  let configName = req.query.configName
  let port = req.query.port
  const { configs } = await parseConfig()
  // ... (kode validasi query tetap sama)

  console.log(`hit newip ${configName}`)
  let result = {}
  try {
    const apiConfig = configs.api
    const appPort = apiConfig.port
    // const apiHostName = apiConfig.apiHostName // DIHAPUS
    
    // Semua variabel dan logika cloudflare dihapus
    // const cloudflareConfig = configs.cloudflare 
    // const domain = cloudflareConfig.domain
    // const email = cloudflareConfig.email
    // const token = cloudflareConfig.token
    
    const hostLocalIp = apiConfig.hostLocalIp
    const hostPublicIp = apiConfig.hostPublicIp
    
    // Variabel 'host' tidak lagi dibuat dari domain
    // const host = `${configName}.${domain}` // DIHAPUS

    const configType = config.get(configName, 'type')
    if (!configType) {
      return res.status(400).json({ success: false, error: 'config not found' })
    }
    // ... (logika runningprocess.txt tetap sama)

    let serverConfig
    if (configType == 'tencent') {
      serverConfig = configs.tencent.find(
        (config) => config.configName == configName
      )
      result = await newIpTencent(serverConfig)
    } else if (configType == 'azure') {
      serverConfig = configs.azure.find(
        (config) => config.configName == configName
      )
      result = await newIpAzure(serverConfig)
    } else if (configType == 'aws') {
      serverConfig = configs.aws.find(
        (config) => config.configName == configName
      )
      result = await newIpAws(serverConfig)
    }
    const socks5Port = serverConfig.socks5Port
    const httpPort = serverConfig.httpPort
    const publicIp = result.newIp // Ini adalah IP baru dari instance (misal: AWS EC2)
    
    // DIUBAH: 'host' sekarang adalah IP publik dari instance yang di-rotate
    const host = publicIp; 

    // DIHAPUS: Blok untuk memodifikasi /etc/hosts tidak lagi diperlukan
    /* const hosts = fs.readFileSync('/etc/hosts', 'utf8')
    const hostsArray = hosts.split('\n')
    const hostIndex = hostsArray.findIndex((line) => line.includes(host))
    if (hostIndex != -1) {
      hostsArray.splice(hostIndex, 1)
      const newHosts = hostsArray.join('\n')
      fs.writeFileSync('/etc/hosts', newHosts)
    }
    fs.appendFileSync('/etc/hosts', `${publicIp} ${host}\n`)
    */

    console.log(
      `profile: ${configName}, old ip: ${result.oldIp}, new ip: ${result.newIp}`
    )
    
    // DIHAPUS: Seluruh blok logika untuk update DNS Cloudflare dihapus
    /*
    const cf = require('cloudflare')({ ... })
    ...
    ...
    */

    const configPath = `/etc/shadowsocks/config_${configName}.json`
    const configTemplate = fs.readFileSync('configtemplate.json', 'utf8')
    const configTemplateJson = JSON.parse(configTemplate)
    
    // DIUBAH: 'server' sekarang diisi dengan IP publik baru, bukan domain
    configTemplateJson.server = publicIp; // Sebelumnya diisi dengan 'host' (domain)
    
    configTemplateJson.server_port = 8388
    configTemplateJson.password = 'Pass'
    configTemplateJson.method = 'aes-128-gcm'
    configTemplateJson.mode = 'tcp_and_udp'
    configTemplateJson.local_address = hostLocalIp
    configTemplateJson.local_port = parseInt(socks5Port)
    configTemplateJson.locals[0].local_address = hostLocalIp
    configTemplateJson.locals[0].local_port = parseInt(httpPort)
    
    // ... (logika pembuatan folder dan file config tetap sama)
    fs.writeFileSync(configPath, JSON.stringify(configTemplateJson))
    console.log(`Config file ${configPath} updated successfully.`)

    // ... (logika pembuatan service systemd tetap sama)

    // ... (logika reload dan start service tetap sama)
    
    // ... (logika pengecekan proxy tetap sama)

    // ... (logika menghapus dari runningprocess.txt tetap sama)

    // DIUBAH: Respons API sekarang menggunakan IP Publik dari server router
    result.proxy = {
      socks5: `${hostPublicIp}:${socks5Port}`,
      http: `${hostPublicIp}:${httpPort}`,
      // 'shadowsocks' sekarang berisi IP baru dari instance sebagai servernya
      shadowsocks: `${publicIp}:8388`,
    }
    
    result.configName = configName
    const endTime = performance.now()
    const executionTime = parseInt((endTime - startTime) / 1000)
    return res.status(200).json({
      success: true,
      result: {
        configName: configName,
        oldIp: result.oldIp,
        newIp: result.newIp,
        proxy: result.proxy,
      },
      executionTime: `${executionTime} seconds`,
    })
  } catch (err) {
    // ... (blok catch error tetap sama)
  }
})

app.get(`/${prefix}/ip`, async (req, res) => {
  // ... (tetap sama)
})

app.get(`/${prefix}/checkConfig`, async (req, res) => {
  console.log('hit checkConfig')
  const { configs } = await parseConfig() // parseConfig yang baru akan dipanggil
  const tencentConfigList = configs.tencent
  // const cloudflareConfig = configs.cloudflare // DIHAPUS
  const awsConfigList = configs.aws
  const azureConfigList = configs.azure
  
  // let cloudflareCheckResult = {} // DIHAPUS
  let tencentCheckResult = []
  let awsCheckResult = []
  let azureCheckResult = []

  try {
    // cloudflareCheckResult = await checkCloudflare(cloudflareConfig) // DIHAPUS
    tencentCheckResult = await Promise.all(
      tencentConfigList.map((config) => checkTencent(config))
    )
    awsCheckResult = await Promise.all(
      awsConfigList.map((config) => checkAws(config))
    )
    azureCheckResult = await Promise.all(
      azureConfigList.map((config) => checkAzure(config))
    )
  } catch (err) {
    return res.status(500).json({ success: false, error: err.message })
  }
  return res.status(200).json({
    success: true,
    result: {
      // cloudflare: cloudflareCheckResult, // DIHAPUS
      tencent: tencentCheckResult,
      aws: awsCheckResult,
      azure: azureCheckResult,
    },
  })
})
