// 物品栏叠加层：旁观附身看不到 HUD，这里用网页画一个（OBS 浏览器源）
// 图标直接从客户端 jar 里读（1.13+ 的模型/贴图都在 jar 里，不在 assets 索引里）
const fs = require('fs')
const path = require('path')
const AdmZip = require('adm-zip')

const MC_DIR = 'E:\\game\\MC\\.minecraft'
const VERSION = process.env.MC_VERSION || '1.20.1'
const JAR = path.join(MC_DIR, 'versions', VERSION, VERSION + '.jar')
const ASSET_INDEX = '5'

// ---------------- 中文名（assets 索引里的 lang/zh_cn.json） ----------------
let CN = null
function loadLang() {
  if (CN) return CN
  CN = {}
  // 优先从客户端 jar 读语言文件（PCL 的 assets 目录可能不完整）
  try {
    const z = jar()
    const e = z && z.getEntry('assets/minecraft/lang/zh_cn.json')
    if (e) {
      CN = JSON.parse(z.readAsText(e))
      return CN
    }
  } catch (e) {}
  try {
    const idx = JSON.parse(fs.readFileSync(path.join(MC_DIR, 'assets', 'indexes', ASSET_INDEX + '.json'), 'utf8'))
    const obj = idx.objects['minecraft/lang/zh_cn.json']
    if (!obj) return CN
    const p = path.join(MC_DIR, 'assets', 'objects', obj.hash.slice(0, 2), obj.hash)
    CN = JSON.parse(fs.readFileSync(p, 'utf8'))
  } catch (e) {
    console.log('[hud] 中文名加载失败（不影响功能）:', e.message)
  }
  return CN
}

function cnName(id) {
  const lang = loadLang()
  return lang['item.minecraft.' + id] || lang['block.minecraft.' + id] || lang['entity.minecraft.' + id] || id
}

// ---------------- 图标（读 jar） ----------------
let ZIP = null
let JAR_BAD = false
function jar() {
  if (!ZIP && !JAR_BAD) {
    try {
      ZIP = new AdmZip(JAR)
    } catch (e) {
      JAR_BAD = true
      console.log('[hud] jar 打不开:', e.message)
    }
  }
  return ZIP
}

function jarJson(name) {
  const z = jar()
  if (!z) return null
  const e = z.getEntry('assets/minecraft/' + name)
  if (!e) return null
  try {
    return JSON.parse(z.readAsText(e))
  } catch (e2) {
    return null
  }
}

function jarPng(name) {
  const z = jar()
  if (!z) return null
  const e = z.getEntry('assets/minecraft/' + name)
  if (!e) return null
  try {
    return z.readFile(e)
  } catch (e2) {
    return null
  }
}

// 从模型 json 里找一张贴图（会沿 parent 往上一两层）
function pickTexture(model) {
  let m = model
  for (let depth = 0; depth < 4 && m; depth++) {
    if (m.textures) {
      for (const k of ['layer0', 'all', 'top', 'particle', 'side', 'texture']) {
        if (m.textures[k]) return m.textures[k]
      }
      const first = Object.values(m.textures)[0]
      if (first) return first
    }
    if (!m.parent) return null
    m = jarJson('models/' + String(m.parent).replace('minecraft:', '') + '.json')
  }
  return null
}

const ICON_CACHE = {}
function iconBuffer(id) {
  if (id in ICON_CACHE) return ICON_CACHE[id]
  let buf = null
  for (const base of ['item', 'block']) {
    const model = jarJson('models/' + base + '/' + id + '.json')
    if (!model) continue
    const tex = pickTexture(model)
    if (!tex) continue
    buf = jarPng('textures/' + String(tex).replace('minecraft:', '') + '.png')
    if (buf) break
  }
  ICON_CACHE[id] = buf
  return buf
}

// ---------------- 数据 ----------------
function data(bot) {
  const slots = []
  for (let i = 0; i < 9; i++) {
    const it = bot.inventory && bot.inventory.slots[36 + i]
    slots.push(
      it
        ? { id: it.name, cn: cnName(it.name), count: it.count, icon: '/icon/' + encodeURIComponent(it.name) + '.png' }
        : null,
    )
  }
  return {
    ok: true,
    health: Math.round(bot.health || 0),
    food: Math.round(bot.food || 0),
    sel: bot.quickBarSlot || 0,
    slots,
  }
}

const HTML = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>hud</title>
<style>
  html,body{margin:0;padding:0;background:transparent;overflow:hidden;height:100%}
  #wrap{position:fixed;left:0;right:0;bottom:10px;display:flex;flex-direction:column;align-items:center;gap:6px;
        font-family:"Microsoft YaHei",sans-serif}
  #stats{display:flex;gap:18px;font-size:15px;color:#fff;text-shadow:2px 2px 0 #000,0 0 6px #000}
  #bar{display:flex;gap:2px}
  .slot{width:68px;height:68px;background:rgba(0,0,0,.55);border:2px solid #6b6b6b;position:relative;
        display:flex;align-items:center;justify-content:center;box-sizing:border-box}
  .slot.sel{border-color:#fff;box-shadow:0 0 10px rgba(255,255,255,.75)}
  .slot.empty{opacity:.35}
  .slot img{width:56px;height:56px;image-rendering:pixelated;pointer-events:none;display:block}
  .cnt{position:absolute;right:3px;bottom:0;font:700 17px sans-serif;color:#fff;text-shadow:1px 1px 0 #000}
</style></head>
<body>
<div id="wrap">
  <div id="stats"><span id="hp">♥ 20</span><span id="fd">🍗 20</span></div>
  <div id="bar"></div>
</div>
<script>
const A = 'http://127.0.0.1:3008'
async function tick(){
  try{
    const d = await (await fetch(A + '/hud.json', {cache:'no-store'})).json()
    document.getElementById('hp').textContent = '♥ ' + d.health
    document.getElementById('fd').textContent = '🍗 ' + d.food
    const bar = document.getElementById('bar')
    bar.innerHTML = d.slots.map((s,i)=>{
      if(!s) return '<div class="slot empty'+(i===d.sel?' sel':'')+'"></div>'
      return '<div class="slot'+(i===d.sel?' sel':'')+'">'+
        '<img src="' + A + s.icon + '">' +
        '<div class="cnt">'+(s.count>1?s.count:'')+'</div></div>'
    }).join('')
  }catch(e){ document.getElementById('bar').textContent = 'HUD 连接失败: ' + e.message }
  setTimeout(tick, 400)
}
tick()
</script>
</body></html>`

module.exports = { HTML, data, iconBuffer, cnName }
