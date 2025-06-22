const express = require('express')
const app = express()
const bodyParser = require('body-parser')
const fs = require('fs')
const morgan = 'morgan'
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
async function newIpAws(serverConfig) { /* ... kode asli ... */ }
async function newIpAzure(serverConfig) { /* ... kode asli ... */ }
async function newIpTencent(serverConfig) { /* ... kode asli ... */ }

// --- FUNGSI parseConfig DISEDERHANAKAN (tanpa key) ---
async function parseConfig() {
  config.read('config.conf')
  const confList = config.sections()
  let tencentConfigList = []
  let azureConfigList = []
  let awsConfigList = []
  let apiConfig = {}
  for (let i = 0; i < confList.length; i++) {
    const configName = confList[i]
    const configType = config.get(confList[i], 'type')
    if (configType == 'tencent') {
        // ... (kode asli tidak berubah)
    } else if (configType == 'api') {
      const prefix = config.get(confList[i], 'prefix')
      const port = config.get(confList[i], 'port')
      const hostLocalIp = config.get(confList[i], 'hostLocalIp')
      const hostPublicIp = config.get(confList[i], 'hostPublicIp')
      // const key = config.get(confList[i], 'key') // DIHAPUS
      apiConfig.prefix = prefix
      apiConfig.port = port
      apiConfig.hostLocalIp = hostLocalIp
      apiConfig.hostPublicIp = hostPublicIp
      // apiConfig.key = key // DIHAPUS
    } else if (configType == 'azure') {
        // ... (kode asli tidak berubah)
    } else if (configType == 'aws') {
        // ... (kode asli tidak berubah)
    }
  }
  return {
    configs: {
      api: apiConfig,
      tencent: tencentConfigList,
      azure: azureConfigList,
      aws: awsConfigList,
    },
  }
}

// --- FUNGSI checkTencent, checkAws, checkAzure TETAP SAMA ---
async function checkTencent(serverConfig) { /*... kode asli ...*/ }
async function checkAws(serverConfig) { /*... kode asli ...*/ }
async function checkAzure(serverConfig) { /*... kode asli ...*/ }


app.get(`/${prefix}/newip/`, async (req, res) => {
  const startTime = performance.now()
  let configName = req.query.configName
  const { configs } = await parseConfig()

  // =================================================================
  // == DIHAPUS: SELURUH BLOK VALIDASI KUNCI API TELAH DIHAPUS ==
  // =================================================================

  // Validasi dasar untuk configName (kode ini sudah ada)
  if (!configName) {
    return res.status(400).json({ success: false, error: 'configName is required' })
  }

  console.log(`hit newip ${configName}`)
  let result = {}
  try {
    const apiConfig = configs.api
    const appPort = apiConfig.port
    const hostLocalIp = apiConfig.hostLocalIp
    const hostPublicIp = apiConfig.hostPublicIp
    
    // ... sisa kode tidak berubah ...

    const configType = config.get(configName, 'type')
    if (!configType) {
      return res.status(400).json({ success: false, error: 'config not found' })
    }
    
    // ... (logika runningprocess.txt tetap sama)

    let serverConfig
    if (configType == 'tencent') {
      // ...
      result = await newIpTencent(serverConfig)
    } else if (configType == 'azure') {
      // ...
      result = await newIpAzure(serverConfig)
    } else if (configType == 'aws') {
      serverConfig = configs.aws.find(
        (config) => config.configName == configName
      )
      result = await newIpAws(serverConfig)
    }
    const socks5Port = serverConfig.socks5Port
    const httpPort = serverConfig.httpPort
    const publicIp = result.newIp 
    
    // ... (logika shadowsocks config update, service restart, dll tetap sama)
    // ... (pastikan semua logika ini ada di kode Anda, saya singkat di sini)
    const configPath = `/etc/shadowsocks/config_${configName}.json`;
    // ... (Update file config shadowsocks)
    fs.writeFileSync(configPath, /* ... content ... */);
    // ... (Restart service)
    
    result.proxy = {
      socks5: `${hostPublicIp}:${socks5Port}`,
      http: `${hostPublicIp}:${httpPort}`,
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
    console.error(`Error processing newip for ${configName}:`, err)
    // ...
  }
})

app.get(`/${prefix}/ip`, async (req, res) => {
  // ... (tetap sama)
})

app.get(`/${prefix}/checkConfig`, async (req, res) => {
  // ... (tetap sama, karena key tidak dicek di sini)
})
