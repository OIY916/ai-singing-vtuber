// 大肥鱼 MC 大脑：状态快照 → DeepSeek 决策 → 动作执行 + 安全保护
// 由 bot.js 在 spawn 后挂载：require('./brain').attach(bot)
const fs = require('fs')
const https = require('https')
const path = require('path')

const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')
const collectBlock = require('mineflayer-collectblock')
const { cnName } = require('./hud.js')

const PLANNER_TICK_MS = 3000 // 空闲时多久想一次
const SAFE_TICK_MS = 800 // 安全层检查频率
const ROOT = 'E:\\qq-bot'

// ---------------- LLM 配置（复用直播服务的 key） ----------------
function loadLLM() {
  const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'stream', 'config.json'), 'utf8'))
  if (cfg.llm_key) {
    return { key: cfg.llm_key, base: cfg.llm_base, model: cfg.llm_model }
  }
  const raw = fs
    .readFileSync(path.join(ROOT, 'data', 'cmd_config.json'), 'utf8')
    .replace(/^\uFEFF/, '')
  const j = JSON.parse(raw)
  const src = (j.provider_sources || []).find((s) => s.id === 'deepseek')
  const key = Array.isArray(src.key) ? src.key[0] : src.key
  return { key, base: src.api_base || 'https://api.deepseek.com', model: 'deepseek-chat' }
}

let LLM = null

function chat(messages, maxTokens = 320) {
  if (!LLM) LLM = loadLLM()
  const body = JSON.stringify({ model: LLM.model, messages, temperature: 0.9, max_tokens: maxTokens })
  const url = new URL(LLM.base.replace(/\/$/, '') + '/chat/completions')
  return new Promise((resolve, reject) => {
    const req = https.request(
      {
        hostname: url.hostname,
        path: url.pathname + url.search,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + LLM.key,
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        let d = ''
        res.on('data', (c) => (d += c))
        res.on('end', () => {
          try {
            const j = JSON.parse(d)
            if (j.error) return reject(new Error(j.error.message || 'api error'))
            resolve(j.choices[0].message.content)
          } catch (e) {
            reject(new Error('LLM 返回解析失败: ' + d.slice(0, 200)))
          }
        })
      },
    )
    req.on('error', reject)
    req.setTimeout(20000, () => req.destroy(new Error('LLM 超时')))
    req.write(body)
    req.end()
  })
}

// ---------------- 状态快照 ----------------
const ORE_NAMES = [
  'coal_ore', 'iron_ore', 'copper_ore', 'gold_ore', 'diamond_ore', 'redstone_ore',
  'lapis_ore', 'emerald_ore', 'oak_log', 'spruce_log', 'birch_log', 'jungle_log',
  'acacia_log', 'dark_oak_log', 'water', 'lava', 'crafting_table', 'furnace',
]
const HOSTILE = ['zombie', 'skeleton', 'creeper', 'spider', 'witch', 'enderman', 'drowned']

function snapshot(bot) {
  const p = bot.entity.position
  const inv = {}
  for (const it of bot.inventory.items()) inv[it.name] = (inv[it.name] || 0) + it.count

  const near = {}
  const base = p.floored()
  for (let dx = -6; dx <= 6; dx++) {
    for (let dy = -3; dy <= 3; dy++) {
      for (let dz = -6; dz <= 6; dz++) {
        const b = bot.blockAt(base.offset(dx, dy, dz))
        if (!b) continue
        if (ORE_NAMES.includes(b.name)) near[b.name] = (near[b.name] || 0) + 1
      }
    }
  }

  const mobs = {}
  for (const id in bot.entities) {
    const e = bot.entities[id]
    if (!e || !e.name || !HOSTILE.includes(e.name)) continue
    const d = e.position.distanceTo(p)
    if (d < 16) mobs[e.name] = Math.round(Math.min(mobs[e.name] ?? 99, d))
  }

  const t = bot.time.time % 24000
  const hp = Math.round(bot.health)
  const fd = Math.round(bot.food)
  const invText = Object.entries(inv).map(([k, v]) => cnName(k) + '×' + v).join('、') || '空'
  const nearText = Object.entries(near).map(([k, v]) => cnName(k) + '×' + v).join('、') || '什么都看不到'
  const mobsText = Object.entries(mobs).map(([k, v]) => cnName(k) + '（' + v + '格）').join('、') || '没有'
  return {
    pos: { x: Math.round(p.x), y: Math.round(p.y), z: Math.round(p.z) },
    health: hp,
    food: fd,
    hpText: hp + '/20',
    fdText: fd + '/20',
    invText,
    nearText,
    mobsText,
    time: t < 13000 ? '白天' : '夜晚',
    inv,
    near,
    mobs,
  }
}

// ---------------- 动作执行 ----------------
const craftFail = {} // 物品名 → 冷却截止时间（失败后 2 分钟不再试）
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// 在脚边找个实心地面，把手里的方块放上去（放工作台用）
async function placeOnGround(bot) {
  const p = bot.entity.position
  const offs = [
    { x: 1, y: -1, z: 0 }, { x: -1, y: -1, z: 0 },
    { x: 0, y: -1, z: 1 }, { x: 0, y: -1, z: -1 },
  ]
  for (const o of offs) {
    const ref = bot.blockAt(p.offset(o.x, o.y, o.z))
    if (!ref || ref.name === 'air' || ref.boundingBox !== 'block') continue
    try {
      await bot.placeBlock(ref, { x: 0, y: 1, z: 0 })
      return true
    } catch (e) {
      /* 换个位置再试 */
    }
  }
  return false
}


// 挖着接近目标（石头里的矿也能挖到手）：朝目标方向清路，靠近了就挖它
async function digToward(bot, target, maxMs) {
  const t0 = Date.now()
  const eyeOff = 1.6
  while (Date.now() - t0 < maxMs) {
    if (!bot.entity) return 'gone'
    const d = target.position.distanceTo(bot.entity.position)
    if (d <= 4.0) return 'near'
    await bot.lookAt(target.position.offset(0.5, 0.5, 0.5), true).catch(() => {})
    const eye = bot.entity.position.offset(0, eyeOff, 0)
    const dir = target.position.offset(0.5, 0.5, 0.5).minus(eye)
    const len = Math.max(0.5, Math.hypot(dir.x, dir.y, dir.z))
    const ux = dir.x / len, uy = dir.y / len, uz = dir.z / len
    let dug = false
    for (const step of [1.0, 1.7, 2.4]) {
      const p = eye.offset(ux * step, uy * step, uz * step).floored()
      const b = bot.blockAt(p)
      if (!b) continue
      if (b.name === 'air' || b.name === 'water' || b.name === 'lava' || b.name === 'bedrock') continue
      if (!b.diggable) continue
      try {
        await bot.dig(b, true)
        dug = true
      } catch (e) {}
      break
    }
    if (!dug) {
      // 前方脚下如果有实心方块，先挖掉再走，避免把自己埋进石头里
      const aheadFeet = bot.blockAt(bot.entity.position.offset(ux * 1.2, 0, uz * 1.2).floored())
      if (aheadFeet && aheadFeet.boundingBox === 'block' && aheadFeet.diggable && !['water', 'lava'].includes(aheadFeet.name)) {
        try {
          await bot.dig(aheadFeet, true)
        } catch (e) {}
        await sleep(150)
        continue
      }
      // 前面是空的，往前挪一小步
      bot.setControlState('forward', true)
      await sleep(350)
      bot.setControlState('forward', false)
      await sleep(150)
    }
    await sleep(120)
  }
  return 'timeout'
}

// ---------------- 通用寻路 ----------------
//
// ⚠️ 修「走两步就停、反复重来」的竞态（2026-10-29）：
//    旧版 setGoal 之后每 0.8 秒查一次 isMoving()，但 pathfinder 是异步算路的，
//    1.5 秒内 isMoving() 还是 false → 立刻判定「没走到」→ 大脑换动作 →
//    下一轮又发同样的 goto → 表现就是「走两步就站住」。
//    现在必须等到：真到了 / 真没路 / 走着走着卡死（算路成功但动不了）。
async function awaitPath(bot, goal, opts = {}) {
  const maxMs = opts.maxMs || 30000
  const gx = Number(opts.gx), gz = Number(opts.gz)
  const arrive = Number(opts.arrive || 2)
  const t0 = Date.now()
  let started = false // pathfinder 开始真的走了
  let noPath = false // 服务器说没有路
  let movedAt = Date.now()

  const onPath = (r) => {
    if (r && r.status === 'noPath') noPath = true
    if (process.env.BRAIN_DEBUG) console.log('[diag] path_update', r && r.status, (r && r.path || []).length)
  }
  bot.on('path_update', onPath)
  try {
    bot.pathfinder.setGoal(goal)
  } catch (e) {
    bot.removeListener('path_update', onPath)
    return { ok: false, why: '寻路目标无效: ' + e.message }
  }
  try {
    while (Date.now() - t0 < maxMs) {
      await sleep(300)
      if (!bot.entity) return { ok: false, why: '人没了' }
      const p = bot.entity.position
      const d = Math.hypot(p.x - gx, p.z - gz)
      if (d <= arrive) return { ok: true, why: '到了' }
      if (bot.pathfinder.isMoving()) {
        if (!started) started = true
        movedAt = Date.now()
      } else if (started) {
        // 走着走着不动了：寻路放弃了（多半是前面有墙又不许挖）
        if (Date.now() - movedAt > 2500) {
          return { ok: false, why: `走了一段就卡住，差 ${Math.round(d)} 格` }
        }
      } else if (noPath && Date.now() - t0 > 2000) {
        return { ok: false, why: `这边没有路，差 ${Math.round(d)} 格` }
      } else if (Date.now() - t0 > 4500) {
        const st = `${Math.round(d)} 格 [started=${started} noPath=${noPath} moving=${bot.pathfinder.isMoving()} pos=${p.x.toFixed(1)},${p.y.toFixed(1)},${p.z.toFixed(1)}]`
        return { ok: false, why: `算不出路（可能太远），差 ${st}` }
      }
    }
    return { ok: false, why: '超时没走到' }
  } finally {
    bot.removeListener('path_update', onPath)
  }
}

function clearControls(bot) {
  ;['forward', 'back', 'left', 'right', 'jump', 'sprint', 'sneak'].forEach((k) => {
    try { bot.setControlState(k, false) } catch (e) { /* 已经是关的 */ }
  })
}

// ⚠️⚠️ 安全的「取消寻路」——2026-10-29 抓到的元凶 ⚠️⚠️
// 绝对不能调用 bot.pathfinder.stop()！
// mineflayer-pathfinder 2.4.5 里它只做 stopPathing = true（index.js:162），
// 而这个标志只在「走路上到达下一个节点」时才被清掉（index.js:582）。
// 下一次 setGoal() 会先走 resetPath()，那里有一句 `if (stopPathing) return stop()`，
// 内部 stop() 会把 stateGoal = null —— 刚设好的目标被立刻丢弃，
// 结果就是「第一轮之后她再也不会自己走路了」：站着不动、原地跳、寻路永远不出结果。
// 正确做法：用 setGoal(null)，它会走完整个 resetPath 并把标志清干净。
function cancelPath(bot) {
  try {
    bot.pathfinder.setGoal(null)
  } catch (e) { /* 还没加载寻路 */ }
}

// 卡死自救：挖开挡路的那一格 + 转个方向走两步（异步执行，不阻塞规划）
function escapeStep(bot) {
  if (!bot.entity) return
  cancelPath(bot) // 安全取消（绝不能用 pathfinder.stop，见下方说明）
  clearControls(bot)
  const yaw = bot.entity.yaw || 0
  const fx = -Math.sin(yaw), fz = Math.cos(yaw)
  const ahead = bot.blockAt(bot.entity.position.offset(fx * 1.2, 0.3, fz * 1.2).floored())
  if (ahead && ahead.boundingBox === 'block' && ahead.diggable && !['water', 'lava'].includes(ahead.name)) {
    bot.lookAt(ahead.position.offset(0.5, 0.5, 0.5), true).catch(() => {})
    bot.dig(ahead, true)
      .then(() => logEvent('自救：挖开了挡路的' + ahead.name))
      .catch((e) => logEvent('自救挖不动: ' + e.message))
    return // 挖完等它自己走出来，别同时又推方向键
  }
  // 没东西可挖：随便转个方向（避开正东正西，容易撞树干）走一小段，顺带跳一下防卡台阶
  const turn = (Math.floor(Math.random() * 4) * Math.PI) / 2 + Math.PI / 4
  bot.look(turn, 0.1).catch(() => {})
  bot.setControlState('forward', true)
  bot.setControlState('jump', true)
  setTimeout(() => { try { bot.setControlState('jump', false) } catch (e) {} }, 450)
  setTimeout(() => { try { bot.setControlState('forward', false) } catch (e) {} }, 1500)
}

// 规划层的兜底动作：同类动作连续失败时强制换个活干
function fallbackAct(bot) {
  const items = bot.inventory.items()
  const food = items.find((i) => /cooked|bread|apple|carrot|potato|beef|porkchop|chicken|mutton|melon|sweet_berries/.test(i.name))
  if (food && bot.food < 18) return { act: 'eat', why: '没别的可干，先吃点东西' }
  const logs = items.find((i) => /_log$/.test(i.name))
  if (logs && bot.inventory.count(bot.registry.itemsByName.crafting_table.id) > 0) {
    return { act: 'place', item: 'crafting_table', why: '把工作台放出来准备做东西' }
  }
  if (logs) return { act: 'craft', item: 'wooden_pickaxe', why: '有木头了，做把镐子' }
  return { act: 'mine', kind: 'wood', why: '附近走不动，先就地砍点树' }
}

// 原木 → 木板（合成链的第一环，缺木板时自动做）
async function autoPlanks(bot) {
  const logs = ['oak_log', 'acacia_log', 'birch_log', 'spruce_log', 'jungle_log', 'dark_oak_log', 'mangrove_log', 'cherry_log']
  const log = bot.inventory.items().find((i) => logs.includes(i.name))
  if (!log) return false
  const plank = bot.registry.itemsByName[log.name.replace('_log', '_planks')]
  if (!plank) return false
  const r = bot.recipesFor(plank.id, null, 1, null)
  if (!r.length) return false
  await bot.craft(r[0], 4, null)
  return true
}
function exec(bot, act) {
  return new Promise((resolve) => {
    let done = false
    // ok=false 表示这个动作失败了（没路/被挡住/没有材料），规划层会拿它做反重复
    const finish = (msg, ok = true) => {
      if (done) return
      done = true
      clearTimeout(timer)
      brain.lastResult = { ok, msg, act: act.act, key: act.act + (act.kind ? ':' + act.kind : '') }
      resolve(msg)
    }
    const timer = setTimeout(() => finish('（超时，动作没做完）', false), 40000)

    try {
      switch (act.act) {
        case 'say':
          bot.chat(String(act.text || '').slice(0, 100))
          return finish('说了：' + act.text)

        case 'wait':
          setTimeout(() => finish('发呆了 ' + (act.sec || 5) + ' 秒'), (act.sec || 5) * 1000)
          return

        case 'eat': {
          const food = bot.inventory
            .items()
            .find((i) => /cooked|bread|apple|carrot|potato|beef|porkchop|chicken|mutton|melon|sweet_berries/.test(i.name))
          if (!food) return finish('没有吃的', false)
          bot.equip(food, 'hand').then(() => bot.consume()).then(() => finish('吃了 ' + food.name)).catch((e) => finish('吃失败: ' + e.message))
          return
        }

        case 'goto': {
          const gx = Number(act.x), gz = Number(act.z)
          if (!isFinite(gx) || !isFinite(gz)) return finish('坐标看不懂: ' + act.x + ',' + act.z, false)
          const g = new goals.GoalNearXZ(gx, gz, 2)
          awaitPath(bot, g, { gx, gz, arrive: 3, maxMs: 35000 }).then((r) => {
            if (r.ok) finish(`走到了 ${gx},${gz}`)
            else {
              cancelPath(bot)
              finish(`没走到 ${gx},${gz}（${r.why}）`, false)
            }
          })
          return
        }

        case 'explore': {
          const map = { north: [0, -1], south: [0, 1], west: [-1, 0], east: [1, 0] }
          const [dx, dz] = map[act.dir] || [0, -1]
          // 目标距离压到 14 格以内：thinkTimeout 只有 1.5 秒，目标太远 A* 必然算不出路
          const dist = Math.min(Number(act.dist) || 14, 20)
          const start = bot.entity.position.clone()
          const tx = Math.floor(start.x + dx * dist)
          const tz = Math.floor(start.z + dz * dist)
          const g = new goals.GoalNearXZ(tx, tz, 3)
          awaitPath(bot, g, { gx: tx, gz: tz, arrive: 6, maxMs: 26000 }).then((r) => {
            const moved = bot.entity ? bot.entity.position.distanceTo(start) : 0
            if (r.ok || moved > 6) {
              finish(`往${act.dir}走了 ${Math.round(moved)} 格`)
            } else {
              cancelPath(bot)
              finish(`往${act.dir}走不动（${r.why}）`, false)
            }
          })
          return
        }

        case 'mine': {
          const kinds = {
            wood: ['oak_log', 'spruce_log', 'birch_log', 'jungle_log', 'acacia_log', 'dark_oak_log'],
            stone: ['stone'],
            coal: ['coal_ore'],
            iron: ['iron_ore'],
            dirt: ['dirt', 'grass_block', 'sand', 'gravel'],
          }
          const names = kinds[act.kind] || kinds.wood
          const target = nearestBlock(bot, names, 24)
          if (!target) return finish(`附近没找到 ${act.kind}`)
          const dist = target.position.distanceTo(bot.entity.position)
          // 近的直接挖（不寻路，零卡顿）
          if (dist <= 4.2) {
            bot.lookAt(target.position.offset(0.5, 0.5, 0.5), true)
            bot.dig(target, true).then(
              () => finish(`挖到了 ${target.name}`),
              (e) => finish(`挖失败: ${e.message}`),
            )
            return
          }
          // 远的：朝目标挖过去（石头里的矿也能拿到），限时
          ;(async () => {
            const r = await digToward(bot, target, 14000)
            if (r === 'near') {
              try {
                await bot.dig(target, true)
                finish(`挖到了 ${target.name}`)
              } catch (e) {
                finish(`挖失败: ${e.message}`)
              }
            } else {
              finish(`离 ${target.name} 太远，挖不过去`)
            }
          })()
          return
        }

        case 'craft': {
          const item = bot.registry.itemsByName[act.item]
          if (!item) return finish('不认识的物品 ' + act.item)
          if (craftFail[act.item] > Date.now()) return finish(`先不合成 ${act.item} 了（刚失败过）`)
          let table = bot.findBlock({ matching: bot.registry.blocksByName.crafting_table?.id, maxDistance: 8 })
          const NEEDS_TABLE = ['wooden_pickaxe', 'wooden_sword', 'wooden_axe', 'wooden_shovel',
            'stone_pickaxe', 'stone_sword', 'stone_axe', 'stone_shovel', 'furnace', 'chest', 'torch']
          const doCraft = async () => {
            if (NEEDS_TABLE.includes(act.item) && !table) {
              const ct = bot.inventory.items().find((i) => i.name === 'crafting_table')
              if (!ct) throw new Error('需要工作台（背包里没有）')
              await bot.equip(ct, 'hand')
              if (!(await placeOnGround(bot))) throw new Error('工作台放不下去')
              await sleep(600)
              table = bot.findBlock({ matching: bot.registry.blocksByName.crafting_table?.id, maxDistance: 8 })
              if (!table) throw new Error('放下去了但找不到工作台')
            }
            let recipes = bot.recipesFor(item.id, null, 1, table || null)
            if (!recipes.length) {
              // 常识：原木先变木板，再谈别的
              const made = await autoPlanks(bot)
              if (made) {
                await sleep(400)
                recipes = bot.recipesFor(item.id, null, 1, table || null)
              }
            }
            if (!recipes.length) throw new Error('缺材料')
            await bot.craft(recipes[0], 1, table || null)
            // ⚠️ 连着合成时必须先关窗口，否则下一次 updateSlot 会超时
            if (bot.currentWindow) {
              try { bot.closeWindow(bot.currentWindow) } catch (e) {}
            }
          }
          doCraft()
            .then(() => { delete craftFail[act.item]; finish('合成 ' + act.item) })
            .catch((e) => {
              craftFail[act.item] = Date.now() + 120000 // 2 分钟内别再试同一个
              finish('合成失败: ' + e.message)
            })
          return
        }

        case 'place': {
          const item = bot.inventory.items().find((i) => i.name === act.item)
          if (!item) return finish('没有 ' + act.item)
          bot.equip(item, 'hand')
            .then(() => placeOnGround(bot))
            .then((ok) => finish(ok ? '放了 ' + act.item : '放不上去（周围没平地）'))
            .catch((e) => finish('放失败: ' + e.message))
          return
        }

        default:
          return finish('不认识的动作 ' + act.act)
      }
    } catch (e) {
      finish('执行出错: ' + e.message)
    }
  })
}

function nearestBlock(bot, names, maxDist) {
  let best = null
  let bestD = Infinity
  for (const name of names) {
    const ids = bot.registry.blocksByName[name]
    if (!ids) continue
    const b = bot.findBlock({ matching: ids.id, maxDistance: maxDist })
    if (!b) continue
    const d = b.position.distanceTo(bot.entity.position)
    if (d < bestD) {
      bestD = d
      best = b
    }
  }
  return best
}

// ---------------- 弹幕即时回复（不等规划循环） ----------------
let lastReplyAt = 0
async function quickReply(bot, user, text) {
  if (Date.now() - lastReplyAt < 2500) return
  lastReplyAt = Date.now()
  brain.lastReplyAt = lastReplyAt
  try {
    const r = await chat([
      {
        role: 'system',
        content:
          '你是大肥鱼，直播间里的蓝发鲸鱼娘，在 Minecraft 里自己玩。说话口语化、有点傲娇、很短（15 字内），不用表情符号，不要客套。',
      },
      { role: 'user', content: `观众「${user}」在游戏里对你说："${text}"。用一句话回他。` },
    ], 60)
    const t = String(r)
      .replace(new RegExp('[\\n"“”]', 'g'), '')
      .trim()
      .slice(0, 40)
    if (t) {
      bot.chat(t)
      brain.chatLog.push({ u: user, t: text, r: t })
      if (brain.chatLog.length > 5) brain.chatLog.shift()
      logEvent(`回弹幕 ${user}：${t}`)
    }
  } catch (e) {
    logEvent('回弹幕失败: ' + e.message)
  }
}

// ---------------- 安全层 ----------------
const brain = {
  bot: null,
  events: [],
  directive: '',
  current: null,
  lastPlanAt: 0,
  dead: false,
  stuckSince: 0,
  stuckFails: 0, // 连续卡死自救次数：3 次就传送走
  lastJumpAt: 0, // 液体里跳跃节流，避免每 0.8 秒无脑跳一次
  lastPos: null,
  timers: [],
  why: '',
  lastActs: [],
  streak: 0,
  lastReplyAt: 0,
  trappedSince: 0,
  chatLog: [],
  // 上一个动作的结果（失败的话规划层会知道「为什么失败」，不再死磕同一个方向）
  lastResult: null,
  lastFailMsg: '',
  failKey: '',
  failRetries: 0,
}

// 记忆落盘：重启后还能记得在干嘛、聊过什么
const STATE_FILE = require('path').join(__dirname, 'brain_state.json')
function saveState() {
  try {
    require('fs').writeFileSync(
      STATE_FILE,
      JSON.stringify({ why: brain.why, lastActs: brain.lastActs, events: brain.events.slice(-8), chatLog: brain.chatLog }),
      'utf8',
    )
  } catch (e) {}
}
function loadState() {
  try {
    const d = JSON.parse(require('fs').readFileSync(STATE_FILE, 'utf8'))
    if (d.why) brain.why = d.why
    if (Array.isArray(d.lastActs)) brain.lastActs = d.lastActs
    if (Array.isArray(d.events)) brain.events = d.events
    if (Array.isArray(d.chatLog)) brain.chatLog = d.chatLog
  } catch (e) {}
}

function clearBrainTimers() {
  for (const t of brain.timers) clearInterval(t)
  brain.timers = []
}

let lastSaveAt = 0
function logEvent(e) {
  brain.events.push(e)
  if (brain.events.length > 10) brain.events.shift()
  console.log('[brain]', e)
  if (Date.now() - lastSaveAt > 3000) {
    lastSaveAt = Date.now()
    saveState()
  }
}

function safetyLoop(bot) {
  if (!bot.entity) return
  const { health, food } = bot

  // 死亡 → 重生（1.19+ 用 client_command actionId=0）
  if (health <= 0) {
    if (!brain.dead) {
      brain.dead = true
      logEvent('死掉了，等重生')
      setTimeout(() => {
        try {
          bot._client.write('client_command', { actionId: 0 })
        } catch (e) {
          console.log('[brain] respawn 失败', e.message)
        }
        brain.dead = false
        logEvent('重生了')
      }, 2000)
    }
    return
  }

  // 卡在液体里 → 往上游
  // ⚠️ 2026-10-29：旧版每 0.8 秒无脑跳一次，泡在水里/站在浅水洼时看起来就是
  //    「一直在原地跳」。现在只在自己没有在上升时才跳，而且加节流。
  const head = bot.blockAt(bot.entity.position.offset(0, 1, 0))
  const feet = bot.blockAt(bot.entity.position)
  if (head && ['water', 'lava'].includes(head.name)) {
    const rising = bot.entity.velocity && bot.entity.velocity.y > 0.05
    if (!rising && Date.now() - brain.lastJumpAt > 900) {
      brain.lastJumpAt = Date.now()
      bot.setControlState('jump', true)
      setTimeout(() => { try { bot.setControlState('jump', false) } catch (e) {} }, 400)
    }
  }

  // 掉血 → 吃东西
  if (health < 8 && food > 0) {
    const f = bot.inventory.items().find((i) => /cooked|bread|apple|carrot|potato|beef|porkchop|chicken|mutton|melon|sweet_berries/.test(i.name))
    if (f) bot.equip(f, 'hand').then(() => bot.consume()).catch(() => {})
  }

  // 被地形吞了（脚和头都在实心方块里）：spreadplayers 传送到附近安全地面，不丢背包
  const feetB = bot.blockAt(bot.entity.position)
  const headB = bot.blockAt(bot.entity.position.offset(0, 1, 0))
  const insideBlock =
    feetB && feetB.boundingBox === 'block' && headB && headB.boundingBox === 'block'
  if (insideBlock) {
    if (!brain.trappedSince) brain.trappedSince = Date.now()
    else if (Date.now() - brain.trappedSince > 3500) {
      brain.trappedSince = 0
      const bp = bot.entity.position
      logEvent('被地形吞了，传送自救')
      bot.chat(`/spreadplayers ${Math.floor(bp.x)} ${Math.floor(bp.z)} 0 20 false ${bot.username}`)
      brain.current = null // 让规划层重新想，别在原地继续发呆
    }
  } else if (brain.trappedSince) {
    brain.trappedSince = 0
  }

  // 卡住检测：位置几乎没变（挖矿/合成/吃东西/发呆本来就该站着，不算）
  const p = bot.entity.position
  const busyAct = ['mine', 'craft', 'place', 'eat', 'wait', 'say'].includes(brain.current?.act)
  if (brain.lastPos && !busyAct) {
    const moved = p.distanceTo(brain.lastPos)
    if (moved < 0.08) {
      if (!brain.stuckSince) brain.stuckSince = Date.now()
      // 7 秒不动就动手：先挖开挡路的方块/换个方向走（老版本这里只会在原地跳一下，
      // 于是规划层重新规划 → 又发同样的走法 → 表现成「站着狂跳」）
      else if (Date.now() - brain.stuckSince > 7000) {
        brain.stuckSince = 0
        brain.stuckFails += 1
        clearControls(bot)
        if (brain.stuckFails >= 3) {
          // 三连卡：原地真没救了，直接传送到附近地面
          brain.stuckFails = 0
          const bp = bot.entity.position
          logEvent('连续卡死 3 次，强制挪位置')
          bot.chat(`/spreadplayers ${Math.floor(bp.x)} ${Math.floor(bp.z)} 0 20 false ${bot.username}`)
          brain.current = null
        } else {
          logEvent(`卡住了，自救（第 ${brain.stuckFails} 次）`)
          escapeStep(bot)
        }
      }
    } else {
      brain.stuckSince = 0
      brain.stuckFails = 0
    }
  } else if (!busyAct) {
    brain.stuckSince = 0
  }
  brain.lastPos = p.clone()
}

// ---------------- 规划层 ----------------
function buildPrompt(bot, snap) {
  const fd = snap.food
  const hunger =
    fd < 6 ? '（非常饿！必须马上吃东西，不然会掉血）'
    : fd < 14 ? '（有点饿了，尽快找东西吃）'
    : '（不饿）'
  const chatLog = brain.chatLog.length
    ? brain.chatLog
        .map((c) => '观众' + c.u + '说：' + c.t + ' → 你回：' + c.r)
        .join(String.fromCharCode(10))
    : '（还没人跟你说话）'
  return `你在玩 Minecraft 生存模式，扮演大肥鱼：蓝头发圆滚滚的鲸鱼娘，傲娇爱撒娇，说话口语化、很短。
你只能从下面动作里选一个，然后输出一行 JSON。

【你现在的状态】
- 血量 ${snap.hpText}　饱食度 ${snap.fdText} ${hunger}
- 位置 (${snap.pos.x}, ${snap.pos.y}, ${snap.pos.z})，${snap.time}
- 背包里：${snap.invText}
- 附近看到：${snap.nearText}
- 周围怪物：${snap.mobsText}

【你正在做的事】${brain.why || '（刚来到这个世界，还没想好干嘛）'}
【刚才的动作】${brain.lastActs.length ? brain.lastActs.join(' → ') : '无'}
【同类动作连续】${brain.streak} 次（到 3 次就该换目标了，别死磕一件事）${brain.lastFailMsg ? String.fromCharCode(10) + '【上个动作失败的原因】' + brain.lastFailMsg + '（别再重复同样的走法/目标）' : ''}

【你记得的最近发生】
${brain.events.length ? brain.events.slice(-6).join(String.fromCharCode(10)) : '（刚来到这个世界）'}

【最近的聊天】
${chatLog}

${brain.directive ? '【观众刚下的指令（优先执行）】' + brain.directive + String.fromCharCode(10) : ''}
【可选动作】
{"act":"mine","kind":"wood|stone|coal|iron|dirt"}  采集最近的资源
{"act":"goto","x":整数,"z":整数}  走到坐标
{"act":"explore","dir":"north|east|south|west","dist":14}  往某个方向探索（方向走不通就换方向）
{"act":"craft","item":"crafting_table|stick|wooden_pickaxe|stone_pickaxe|torch"}  合成
{"act":"place","item":"crafting_table"}  在脚边放方块
{"act":"eat"}  吃东西
{"act":"say","text":"一句话"}  对观众说话
{"act":"wait","sec":5}  发呆

规则：
1. 饱食度低于 14 就赶紧吃（背包里有食物时 act 用 eat）；血量低先保命。
2. 观众指令优先；没指令就自主发展（砍树→做工具→挖矿）。
3. 别连续做同一件事超过 3 次。
4. 每个动作带 "why"（一句话说明在干嘛）。
只输出 JSON，不要解释，不要 markdown。`
}

async function plan(bot) {
  const snap = snapshot(bot)
  const prompt = buildPrompt(bot, snap)
  const reply = await chat([
    { role: 'system', content: '你是 Minecraft 玩家 AI，只输出一行 JSON 动作。' },
    { role: 'user', content: prompt },
  ])
  return parseAct(reply)
}

function parseAct(reply) {
  const m = reply.match(/\{[\s\S]*\}/)
  const raw = m ? m[0] : null
  if (!raw) throw new Error('没解析出 JSON: ' + reply.slice(0, 120))
  try {
    return JSON.parse(raw)
  } catch (e) {
    // 被 max_tokens 截断时补个右括号再试一次
    try {
      return JSON.parse(raw.replace(/[\s,]*$/, '') + '}')
    } catch (e2) {
      throw new Error('JSON 坏了: ' + raw.slice(0, 120))
    }
  }
}

async function plannerLoop(bot) {
  if (brain.current || Date.now() - brain.lastPlanAt < PLANNER_TICK_MS) return
  if (!bot.entity || bot.health <= 0) return
  brain.lastPlanAt = Date.now()
  // 每轮开始先清掉残留的寻路目标，避免上一次的 goal 一直拽着她
  // ⚠️ 必须用 cancelPath：pathfinder.stop() 会让寻路永久瘫痪（见 cancelPath 说明）
  cancelPath(bot)
  let act
  try {
    act = await plan(bot)
  } catch (e) {
    logEvent('想不出动作: ' + e.message)
    return
  }
  // 反重复：同一类动作连着做就该换活干
  // key 里带上方向：往北走不动 ≠ 往东走不动，别把方向也一起重复死磕
  const key = act.act + (act.kind ? ':' + act.kind : '') + (act.dir ? ':' + act.dir : '')
  if (key === (brain.lastActs[brain.lastActs.length - 1] || '')) brain.streak += 1
  else brain.streak = 1
  brain.lastActs.push(key)
  if (brain.lastActs.length > 5) brain.lastActs.shift()

  // 反死磕：上一个动作失败了，这次又要干同一件事（方向也一样），
  // 连着第二次就别硬来了 —— 这是「站着原地跳」最主要的来源。
  if (brain.lastResult && !brain.lastResult.ok && key === brain.failKey) {
    brain.failRetries += 1
    if (brain.failRetries >= 2) {
      const fb = fallbackAct(bot)
      logEvent(`刚才「${brain.lastFailMsg}」，不重复，换：${fb.why}`)
      act = fb
      brain.failRetries = 0
      brain.streak = 1
    }
  } else {
    brain.failRetries = 0
  }

  brain.why = act.why || brain.why
  if (brain.streak >= 4 && act.act === 'mine') {
    const dirs = ['north', 'east', 'south', 'west']
    act = { act: 'explore', dir: dirs[Math.floor(Math.random() * 4)], dist: 14, why: '木头砍够了，换个地方看看' }
    brain.streak = 1
    logEvent('连续挖太久了，换个地方')
  } else if (brain.streak >= 3 && act.act === 'explore') {
    // 同一个方向探索老是走不动（多半被地形堵死），强制换个方向/换个活干
    const dirs = ['north', 'east', 'south', 'west']
    const lastDir = brain.lastActs[brain.lastActs.length - 2] || ''
    const cand = dirs.filter((d) => !lastDir.endsWith(':' + d))
    act = { act: 'explore', dir: cand[Math.floor(Math.random() * cand.length)] || 'north', dist: 14, why: '这条路走不通，换一边试试' }
    brain.streak = 1
  }

  brain.current = act
  if (brain.directive) {
    logEvent('接收指令: ' + brain.directive)
    brain.directive = ''
  }
  console.log('[brain] 决定:', JSON.stringify(act))
  const result = await exec(bot, act)
  brain.current = null
  // 记下失败原因，规划层下一轮把它喂给 LLM（「为什么换方向」）
  if (brain.lastResult && brain.lastResult.ok) {
    brain.lastFailMsg = ''
    brain.failKey = ''
    brain.failRetries = 0
  } else if (brain.lastResult) {
    brain.lastFailMsg = brain.lastResult.msg
    brain.failKey = brain.lastResult.key
  }
  logEvent(result)
}

function attach(bot) {
  // ⚠️ 每次重连都会调用 attach：必须先清掉上一轮的定时器，
  //    否则每重连一次就多一套规划/安全循环 → 19 分钟 JS OOM
  clearBrainTimers()
  loadState() // 跨重启的记忆
  brain.bot = bot
  brain.current = null
  brain.lastPlanAt = 0
  bot.loadPlugin(pathfinder)
  bot.loadPlugin(collectBlock.plugin) // 入口是 .plugin，直接传模块会报 "plugin needs to be a function"

  const init = () => {
    const movements = new Movements(bot)
    // ⚠️ 2026-10-29：之前 canDig=false，遇到树干/土坡 A* 直接「没有路」，
    //    她就被原地困住（表现：站着不动或原地跳）。现在允许挖，但把挖的代价
    //    设高：泥土/木头(1.75×digCost) 只在走投无路时才会去挖，
    //    石头(10×digCost) 基本不会挖 → 既能脱困，又不会把自己挖进矿洞里。
    movements.canDig = true
    movements.digCost = 2
    movements.canPlace = false // 不搭方块：搭桥又慢又容易把自己堵死
    movements.placeCost = 5 // 兜底：万一版本里没有 canPlace 开关，靠高代价压住搭路
    movements.maxDropDown = 3
    movements.allowSprinting = true
    bot.pathfinder.setMovements(movements)
    // 主线程是同步算的，必须压死：超过 1.2 秒就放弃（超时后会返回部分路径，先走着）
    bot.pathfinder.thinkTimeout = 1200
    bot.pathfinder.tickTimeout = 800
    logEvent('大脑启动')

    brain.timers.push(
      setInterval(() => {
        try {
          safetyLoop(bot)
        } catch (e) {
          console.log('[brain] 安全层异常', e.message)
        }
      }, SAFE_TICK_MS),
    )

    brain.timers.push(
      setInterval(() => {
        plannerLoop(bot).catch((e) => console.log('[brain] 规划异常', e.message))
      }, 2000),
    )
  }

  // attach 可能在 spawn 之后才调用，两种都兼容
  if (bot.entity) init()
  else bot.once('spawn', init)

  bot.on('death', () => {
    brain.current = null
  })

  // 断线时把定时器清掉，等重连后 attach 再重建
  bot.on('end', clearBrainTimers)

  bot.on('chat', (u, m) => {
    // 只处理真实玩家说的话：系统消息 / 传送提示 / 自己说的都跳过
    if (!u || u === bot.username) return
    if (!bot.players || !bot.players[u]) return
    if (!m || m.startsWith('/')) return
    brain.directive = m
    logEvent(`听到 ${u} 说：${m}`)
    quickReply(bot, u, m).catch((e) => logEvent('回弹幕异常: ' + e.message))
  })
}

module.exports = {
  attach,
  setDirective: (t) => (brain.directive = t),
  state: () => brain,
  debugPrompt: () => (brain.bot && brain.bot.entity ? buildPrompt(brain.bot, snapshot(brain.bot)) : '未连接'),
}
